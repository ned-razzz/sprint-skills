#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TEMP_ROOT = Path("/tmp/export-confluence-docs")
PAGE_ID_RE = re.compile(r'^confluence_page_id:\s*"?(?P<value>[^"\n]+)"?\s*$', re.MULTILINE)
ATTR_RE = re.compile(r'([a-zA-Z0-9_:-]+)="([^"]*)"')
PLACEHOLDER_RE = re.compile(
    r'<!--\s*confluence-drawio(?!-rendered)\b(?P<attrs>.*?)-->\s*',
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
NON_WORD_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class MarkerMatch:
    attrs: dict[str, str]
    start: int
    end: int


@dataclass(frozen=True)
class Node:
    cell_id: str
    label: str
    kind: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str


@dataclass(frozen=True)
class Container:
    cell_id: str
    label: str
    children: tuple[str, ...]
    x: float
    y: float
    width: float
    height: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render draw.io XML files back into a Markdown draft as Mermaid blocks.",
    )
    parser.add_argument("--doc", required=True, help="Path to the Markdown draft.")
    parser.add_argument(
        "--xml-dir",
        help="Directory that contains exported XML files. Defaults to /tmp/export-confluence-docs/<doc-stem>--<page_id>.",
    )
    parser.add_argument("--stdout", action="store_true", help="Print the final Markdown to stdout.")
    parser.add_argument("--check", action="store_true", help="Exit with code 2 when the document would change.")
    return parser.parse_args()


def split_front_matter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("front matter closing delimiter not found")
    return text[: end + 5], text[end + 5 :]


def parse_attrs(raw: str) -> dict[str, str]:
    return {key: value for key, value in ATTR_RE.findall(raw)}


def collect_markers(body: str) -> list[MarkerMatch]:
    markers = [
        MarkerMatch(parse_attrs(match.group("attrs")), match.start(), match.end())
        for match in PLACEHOLDER_RE.finditer(body)
    ]
    markers.sort(key=lambda marker: (marker.start, marker.end))

    previous_end = -1
    for marker in markers:
        if marker.start < previous_end:
            raise ValueError("document contains overlapping draw.io markers")
        previous_end = marker.end
    return markers


def resolve_xml_dir(doc_path: Path, front_matter: str, explicit_xml_dir: str | None) -> Path:
    if explicit_xml_dir:
        return Path(explicit_xml_dir)
    page_id_match = PAGE_ID_RE.search(front_matter)
    if page_id_match is None:
        raise ValueError("confluence_page_id not found")
    return DEFAULT_TEMP_ROOT / f"{doc_path.stem}--{page_id_match.group('value').strip()}"


def xml_names_by_slug(xml_names: list[str]) -> dict[str, str]:
    names_by_slug: dict[str, str] = {}
    for xml_name in xml_names:
        slug = Path(xml_name).stem.casefold()
        if slug in names_by_slug:
            raise ValueError(f"multiple xml files share diagram slug '{slug}'")
        names_by_slug[slug] = xml_name
    return names_by_slug


def resolve_marker_xml(attrs: dict[str, str], names_by_slug: dict[str, str]) -> str:
    diagram_slug = attrs.get("diagram_slug", "").strip()
    if not diagram_slug:
        raise ValueError(f"diagram_slug is required for marker {attrs!r}")
    candidate = names_by_slug.get(diagram_slug.casefold())
    if candidate:
        return candidate
    raise ValueError(f"xml file could not be resolved for diagram_slug '{diagram_slug}'")


def sanitize_label(value: str) -> str:
    text = html.unescape(value or "")
    text = BR_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    return " ".join(text.split())


def parse_number(value: str | None) -> float:
    try:
        return float(value or "0")
    except ValueError:
        return 0.0


def classify_kind(style: str, label: str) -> str:
    lowered = label.casefold()
    if "text;" in style:
        return "text"
    if "shape=cylinder3" in style or "shape=cylinder" in style:
        return "database"
    if "ellipse;" in style:
        return "peripheral"
    if "service" in lowered:
        return "service"
    if "controller" in lowered:
        return "controller"
    return "component"


def read_geometry(cell: ET.Element) -> tuple[float, float, float, float]:
    geometry = cell.find("mxGeometry")
    if geometry is None:
        return 0.0, 0.0, 0.0, 0.0
    return (
        parse_number(geometry.attrib.get("x")),
        parse_number(geometry.attrib.get("y")),
        parse_number(geometry.attrib.get("width")),
        parse_number(geometry.attrib.get("height")),
    )


def load_cells(xml_path: Path) -> list[ET.Element]:
    root = ET.parse(xml_path).getroot()
    if root.tag != "mxGraphModel":
        nested = root.find(".//mxGraphModel")
        if nested is None:
            raise ValueError(f"mxGraphModel not found in {xml_path.name}")
        root = nested
    graph_root = root.find("root")
    if graph_root is None:
        raise ValueError(f"graph root not found in {xml_path.name}")
    return list(graph_root)


def parse_diagram(xml_path: Path) -> tuple[dict[str, Node], list[Edge], list[Container]]:
    cells = load_cells(xml_path)
    edge_labels: dict[str, list[str]] = {}
    all_nodes: dict[str, Node] = {}
    unlabeled_nodes: set[str] = set()

    for cell in cells:
        if cell.attrib.get("vertex") != "1":
            continue
        style = cell.attrib.get("style", "")
        label = sanitize_label(cell.attrib.get("value", ""))
        if "edgeLabel" in style:
            parent_id = cell.attrib.get("parent", "")
            if label and parent_id:
                edge_labels.setdefault(parent_id, []).append(label)
            continue
        x, y, width, height = read_geometry(cell)
        node = Node(
            cell_id=cell.attrib["id"],
            label=label,
            kind=classify_kind(style, label),
            x=x,
            y=y,
            width=width,
            height=height,
        )
        if label:
            all_nodes[node.cell_id] = node
        else:
            unlabeled_nodes.add(node.cell_id)

    edges: list[Edge] = []
    incident_ids: set[str] = set()
    for cell in cells:
        if cell.attrib.get("edge") != "1":
            continue
        source = cell.attrib.get("source")
        target = cell.attrib.get("target")
        if not source or not target:
            raise ValueError(f"edge without source or target in {xml_path.name}")
        if source in unlabeled_nodes or target in unlabeled_nodes:
            raise ValueError(f"edge connected to unlabeled vertex in {xml_path.name}")
        if source not in all_nodes or target not in all_nodes:
            raise ValueError(f"edge connected to non-renderable vertex in {xml_path.name}")
        raw_label = sanitize_label(cell.attrib.get("value", ""))
        label = raw_label or " / ".join(edge_labels.get(cell.attrib.get("id", ""), []))
        edges.append(Edge(source=source, target=target, label=label))
        incident_ids.add(source)
        incident_ids.add(target)

    renderable = {cell_id: node for cell_id, node in all_nodes.items() if node.kind != "text"}
    if not renderable:
        raise ValueError(f"no renderable nodes found in {xml_path.name}")

    containers: list[Container] = []
    for node in renderable.values():
        if node.cell_id in incident_ids:
            continue
        if node.width < 220 or node.height < 150:
            continue
        outer_area = node.width * node.height
        child_ids: list[str] = []
        for candidate in renderable.values():
            if candidate.cell_id == node.cell_id:
                continue
            if candidate.width * candidate.height >= outer_area:
                continue
            center_x = candidate.x + (candidate.width / 2.0)
            center_y = candidate.y + (candidate.height / 2.0)
            if node.x <= center_x <= node.x + node.width and node.y <= center_y <= node.y + node.height:
                child_ids.append(candidate.cell_id)
        if len(child_ids) >= 2:
            containers.append(
                Container(
                    cell_id=node.cell_id,
                    label=node.label,
                    children=tuple(sorted(child_ids)),
                    x=node.x,
                    y=node.y,
                    width=node.width,
                    height=node.height,
                )
            )

    return renderable, edges, sorted(containers, key=lambda item: (item.y, item.x, item.label))


def escape_mermaid(value: str) -> str:
    return value.replace('"', "'").replace("[", "(").replace("]", ")").strip()


def make_identifier(seed: str, seen: dict[str, int], prefix: str) -> str:
    base = NON_WORD_RE.sub("_", seed.casefold()).strip("_") or prefix
    if base[0].isdigit():
        base = f"{prefix}_{base}"
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}_{seen[base]}"


def node_shape(node_id: str, node: Node) -> str:
    label = escape_mermaid(node.label)
    if node.kind == "database":
        return f'{node_id}[("{label}")]'
    if node.kind == "peripheral":
        return f'{node_id}(("{label}"))'
    return f'{node_id}["{label}"]'


def choose_parents(containers: list[Container]) -> tuple[dict[str, str | None], dict[str, str | None]]:
    by_id = {item.cell_id: item for item in containers}
    container_parents: dict[str, str | None] = {}
    node_parents: dict[str, str | None] = {}

    def parent_candidates(target: str) -> list[Container]:
        return [container for container in containers if target in container.children]

    for container in containers:
        candidates = [item for item in parent_candidates(container.cell_id) if item.cell_id != container.cell_id]
        parent = min(
            candidates,
            default=None,
            key=lambda item: (item.width * item.height, item.y, item.x, item.label),
        )
        container_parents[container.cell_id] = None if parent is None else parent.cell_id

    for container in containers:
        for child_id in container.children:
            if child_id in by_id:
                continue
            candidates = parent_candidates(child_id)
            parent = min(
                candidates,
                default=None,
                key=lambda item: (item.width * item.height, item.y, item.x, item.label),
            )
            node_parents[child_id] = None if parent is None else parent.cell_id

    return container_parents, node_parents


def render_mermaid(xml_path: Path) -> str:
    nodes, edges, containers = parse_diagram(xml_path)
    container_ids = {item.cell_id for item in containers}
    container_parents, node_parents = choose_parents(containers)

    seen_node_ids: dict[str, int] = {}
    node_ids = {
        cell_id: make_identifier(node.label, seen_node_ids, "node")
        for cell_id, node in sorted(nodes.items(), key=lambda item: (item[1].y, item[1].x, item[1].label))
    }
    seen_group_ids: dict[str, int] = {}
    group_ids = {item.cell_id: f"sg_{make_identifier(item.label, seen_group_ids, 'group')}" for item in containers}

    groups_by_parent: dict[str | None, list[Container]] = {}
    for container in containers:
        groups_by_parent.setdefault(container_parents[container.cell_id], []).append(container)
    for items in groups_by_parent.values():
        items.sort(key=lambda item: (item.y, item.x, item.label))

    nodes_by_parent: dict[str | None, list[Node]] = {}
    for cell_id, node in nodes.items():
        if cell_id in container_ids:
            continue
        nodes_by_parent.setdefault(node_parents.get(cell_id), []).append(node)
    for items in nodes_by_parent.values():
        items.sort(key=lambda item: (item.y, item.x, item.label))

    lines = ["flowchart TB"]

    def emit_group(group: Container, indent: str) -> None:
        lines.append(f'{indent}subgraph {group_ids[group.cell_id]}["{escape_mermaid(group.label)}"]')
        for child_group in groups_by_parent.get(group.cell_id, []):
            emit_group(child_group, indent + "  ")
        for node in nodes_by_parent.get(group.cell_id, []):
            lines.append(f"{indent}  {node_shape(node_ids[node.cell_id], node)}")
        lines.append(f"{indent}end")

    for group in groups_by_parent.get(None, []):
        emit_group(group, "  ")
    for node in nodes_by_parent.get(None, []):
        lines.append(f"  {node_shape(node_ids[node.cell_id], node)}")

    edge_lines: list[str] = []
    for edge in edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            continue
        if edge.label:
            edge_lines.append(
                f"{node_ids[edge.source]} -->|{escape_mermaid(edge.label)}| {node_ids[edge.target]}"
            )
        else:
            edge_lines.append(f"{node_ids[edge.source]} --> {node_ids[edge.target]}")
    for line in sorted(set(edge_lines)):
        lines.append(f"  {line}")

    return "\n".join(lines)


def render_marker_block(mermaid: str) -> str:
    return f"```mermaid\n{mermaid.rstrip()}\n```\n"


def render_document(doc_path: Path, xml_dir: Path) -> str:
    source = doc_path.read_text(encoding="utf-8")
    front_matter, body = split_front_matter(source)
    markers = collect_markers(body)

    if not xml_dir.is_dir():
        raise ValueError(f"xml directory not found: {xml_dir}")
    xml_names = sorted(path.name for path in xml_dir.glob("*.xml") if path.is_file())
    if not xml_names:
        return source if source.endswith("\n") else f"{source}\n"

    names_by_slug = xml_names_by_slug(xml_names)
    used_xml_names: set[str] = set()
    rendered_by_name: dict[str, str] = {}
    output_parts: list[str] = []
    cursor = 0

    for marker in markers:
        output_parts.append(body[cursor : marker.start])
        xml_name = resolve_marker_xml(marker.attrs, names_by_slug)
        used_xml_names.add(xml_name)
        if xml_name not in rendered_by_name:
            rendered_by_name[xml_name] = render_mermaid(xml_dir / xml_name)
        output_parts.append(render_marker_block(rendered_by_name[xml_name]))
        cursor = marker.end

    output_parts.append(body[cursor:])
    output = front_matter + "".join(output_parts)

    unused_xml_names = [name for name in xml_names if name not in used_xml_names]
    if unused_xml_names:
        raise ValueError(f"unused xml files: {', '.join(unused_xml_names)}")
    if not output.endswith("\n"):
        output += "\n"
    return output


def main() -> int:
    try:
        args = parse_args()
        doc_path = Path(args.doc)
        original = doc_path.read_text(encoding="utf-8")
        front_matter, _ = split_front_matter(original)
        xml_dir = resolve_xml_dir(doc_path, front_matter, args.xml_dir)
        rendered = render_document(doc_path, xml_dir)

        if args.stdout:
            sys.stdout.write(rendered)
            return 0
        if args.check:
            if rendered != original:
                print(json.dumps({"changed": [str(doc_path)]}, ensure_ascii=False, indent=2))
                return 2
            return 0

        doc_path.write_text(rendered, encoding="utf-8")
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
