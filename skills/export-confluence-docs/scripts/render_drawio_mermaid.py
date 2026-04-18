#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from extract_drawio_ir import extract_ir

SLUG_RE = re.compile(r"[^a-z0-9]+")
EXPLICIT_UNSUPPORTED_PATTERNS = (r"\[\*\]", r"<<.*>>", r"^\s*class\s+", r"^\s*state\s+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render deterministic Mermaid flowcharts from mapped draw.io XML.")
    parser.add_argument("--map-json", required=True, help="Map JSON file path or - for stdin.")
    parser.add_argument("--xml-dir", help="Override xml_dir from the map JSON.")
    parser.add_argument("--out", default="-", help="Output diagram JSON path, or - for stdout.")
    return parser.parse_args()


def load_map(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("map JSON must be an object")
    document = raw.get("document")
    sections = raw.get("sections")
    xml_files = raw.get("xml_files")
    if not isinstance(document, dict):
        raise ValueError("map JSON must include a document object")
    if not isinstance(sections, list):
        raise ValueError("map JSON must include a sections array")
    if not isinstance(xml_files, list):
        raise ValueError("map JSON must include an xml_files array")
    return raw


def normalize_identifier(value: str, prefix: str) -> str:
    slug = SLUG_RE.sub("_", value.casefold()).strip("_")
    if not slug:
        slug = "item"
    if slug[0].isdigit():
        slug = f"{prefix}_{slug}"
    return slug


def node_identifier(label: str, used: dict[str, int]) -> str:
    base = normalize_identifier(label, "node")
    count = used.get(base, 0) + 1
    used[base] = count
    if count == 1:
        return base
    return f"{base}_{count}"


def escape_label(value: str) -> str:
    return value.replace('"', "'").replace("[", "(").replace("]", ")").strip()


def detect_explicitly_unsupported(nodes: list[dict]) -> list[str]:
    rejected = []
    for node in nodes:
        label = node["label"]
        for pattern in EXPLICIT_UNSUPPORTED_PATTERNS:
            if re.search(pattern, label, flags=re.IGNORECASE):
                rejected.append(label)
                break
    return sorted(set(rejected))


def validate_ir(ir: dict) -> tuple[dict[str, dict], list[dict]]:
    issues = ir.get("issues") or {}
    unsupported_edges = issues.get("unsupported_edges") or []
    if unsupported_edges:
        details = ", ".join(
            f"{item.get('id') or '?'}: {item['reason']}" for item in unsupported_edges[:3]
        )
        raise ValueError(f"diagram contains unsupported edges ({details})")

    connected_unlabeled = issues.get("connected_unlabeled_vertices") or []
    if connected_unlabeled:
        labels = ", ".join(item["id"] for item in connected_unlabeled[:3])
        raise ValueError(f"diagram contains connected unlabeled vertices ({labels})")

    nodes = ir.get("nodes") or []
    explicit_rejections = detect_explicitly_unsupported(nodes)
    if explicit_rejections:
        joined = ", ".join(explicit_rejections[:3])
        raise ValueError(f"diagram looks non-architectural ({joined})")

    node_by_id = {node["id"]: node for node in nodes}
    renderable_nodes = {node_id: node for node_id, node in node_by_id.items() if node["kind"] != "text"}
    if not renderable_nodes:
        raise ValueError("diagram has no renderable architecture nodes")

    text_edge_nodes = sorted(
        {
            node_id
            for edge in ir.get("edges") or []
            for node_id in (edge["source"], edge["target"])
            if node_by_id[node_id]["kind"] == "text"
        }
    )
    if text_edge_nodes:
        joined = ", ".join(text_edge_nodes[:3])
        raise ValueError(f"diagram uses text-only nodes in edges ({joined})")

    return node_by_id, renderable_nodes


def choose_containers(ir: dict, renderable_nodes: dict[str, dict]) -> list[dict]:
    container_candidates = []
    renderable_ids = set(renderable_nodes)
    for container in ir.get("containers") or []:
        child_ids = [child_id for child_id in container.get("children", []) if child_id in renderable_ids]
        if len(child_ids) < 2:
            continue
        container_candidates.append(
            {
                "id": container["id"],
                "label": container["label"],
                "children": child_ids,
                "geometry": container["geometry"],
            }
        )
    return container_candidates


def build_container_maps(containers: list[dict]) -> tuple[dict[str, str | None], dict[str, str | None]]:
    container_by_id = {container["id"]: container for container in containers}

    def parents_for(target_id: str) -> list[dict]:
        return [container for container in containers if target_id in container["children"]]

    def ensure_nested(candidates: list[dict], target_id: str) -> None:
        for index, left in enumerate(candidates):
            for right in candidates[index + 1 :]:
                left_contains_right = right["id"] in left["children"]
                right_contains_left = left["id"] in right["children"]
                if not left_contains_right and not right_contains_left:
                    raise ValueError(
                        f"diagram has overlapping container semantics for '{target_id}'"
                    )

    container_parent_by_id: dict[str, str | None] = {}
    node_parent_by_id: dict[str, str | None] = {}

    for container in containers:
        candidates = [candidate for candidate in parents_for(container["id"]) if candidate["id"] != container["id"]]
        ensure_nested(candidates, container["label"])
        parent = min(
            candidates,
            default=None,
            key=lambda item: (
                item["geometry"]["width"] * item["geometry"]["height"],
                item["geometry"]["y"],
                item["geometry"]["x"],
                item["label"],
            ),
        )
        container_parent_by_id[container["id"]] = parent["id"] if parent else None

    child_container_ids = set(container_by_id)
    for container in containers:
        for child_id in container["children"]:
            if child_id in child_container_ids:
                continue
            candidates = parents_for(child_id)
            ensure_nested(candidates, child_id)
            parent = min(
                candidates,
                default=None,
                key=lambda item: (
                    item["geometry"]["width"] * item["geometry"]["height"],
                    item["geometry"]["y"],
                    item["geometry"]["x"],
                    item["label"],
                ),
            )
            node_parent_by_id[child_id] = parent["id"] if parent else None

    return container_parent_by_id, node_parent_by_id


def node_declaration(identifier: str, label: str, kind: str) -> str:
    safe_label = escape_label(label)
    if kind == "database":
        return f'{identifier}[("{safe_label}")]'
    if kind == "peripheral":
        return f'{identifier}(("{safe_label}"))'
    return f'{identifier}["{safe_label}"]'


def edge_statement(source_id: str, target_id: str, label: str) -> str:
    if label:
        return f"{source_id} -->|{escape_label(label)}| {target_id}"
    return f"{source_id} --> {target_id}"


def render_mermaid(ir: dict) -> str:
    node_by_id, renderable_nodes = validate_ir(ir)
    containers = choose_containers(ir, renderable_nodes)
    container_by_id = {container["id"]: container for container in containers}
    container_parent_by_id, node_parent_by_id = build_container_maps(containers)

    used_identifiers: dict[str, int] = {}
    mermaid_id_by_node: dict[str, str] = {}
    for node in sorted(renderable_nodes.values(), key=lambda item: (item["geometry"]["y"], item["geometry"]["x"], item["label"])):
        mermaid_id_by_node[node["id"]] = node_identifier(node["label"], used_identifiers)

    used_subgraph_identifiers: dict[str, int] = {}
    subgraph_id_by_container = {
        container["id"]: f"sg_{node_identifier(container['label'], used_subgraph_identifiers)}"
        for container in containers
    }

    child_containers_by_parent: dict[str | None, list[dict]] = {}
    for container in containers:
        child_containers_by_parent.setdefault(container_parent_by_id[container["id"]], []).append(container)

    child_nodes_by_parent: dict[str | None, list[dict]] = {}
    for node in renderable_nodes.values():
        if node["id"] in container_by_id:
            continue
        child_nodes_by_parent.setdefault(node_parent_by_id.get(node["id"]), []).append(node)

    for items in child_containers_by_parent.values():
        items.sort(key=lambda item: (item["geometry"]["y"], item["geometry"]["x"], item["label"]))
    for items in child_nodes_by_parent.values():
        items.sort(key=lambda item: (item["geometry"]["y"], item["geometry"]["x"], item["label"]))

    lines = ["flowchart TB"]

    def render_container(container: dict, indent: str) -> None:
        lines.append(
            f'{indent}subgraph {subgraph_id_by_container[container["id"]]}["{escape_label(container["label"])}"]'
        )
        for child_container in child_containers_by_parent.get(container["id"], []):
            render_container(child_container, indent + "  ")
        for node in child_nodes_by_parent.get(container["id"], []):
            lines.append(
                f'{indent}  {node_declaration(mermaid_id_by_node[node["id"]], node["label"], node["kind"])}'
            )
        lines.append(f"{indent}end")

    for container in child_containers_by_parent.get(None, []):
        render_container(container, "  ")
    for node in child_nodes_by_parent.get(None, []):
        lines.append(f'  {node_declaration(mermaid_id_by_node[node["id"]], node["label"], node["kind"])}')

    rendered_edges = []
    for edge in ir.get("edges") or []:
        if edge["source"] not in renderable_nodes or edge["target"] not in renderable_nodes:
            continue
        rendered_edges.append(
            (
                mermaid_id_by_node[edge["source"]],
                mermaid_id_by_node[edge["target"]],
                edge["label"],
                edge_statement(
                    mermaid_id_by_node[edge["source"]],
                    mermaid_id_by_node[edge["target"]],
                    edge["label"],
                ),
            )
        )
    rendered_edges.sort(key=lambda item: (item[0], item[1], item[2]))
    for _, _, _, statement in rendered_edges:
        lines.append(f"  {statement}")

    return "\n".join(lines)


def resolve_xml_dir(mapping: dict, explicit_xml_dir: str | None) -> Path:
    if explicit_xml_dir:
        return Path(explicit_xml_dir)
    document = mapping["document"]
    xml_dir = document.get("xml_dir")
    if not isinstance(xml_dir, str) or not xml_dir.strip():
        raise ValueError("map JSON document must include xml_dir")
    return Path(xml_dir)


def build_diagram_payload(mapping: dict, explicit_xml_dir: str | None = None) -> dict:
    mapping = load_map(mapping)
    xml_dir = resolve_xml_dir(mapping, explicit_xml_dir)
    if not xml_dir.is_dir():
        raise ValueError(f"xml directory not found: {xml_dir}")

    ordered_xml_names: list[str] = []
    seen: set[str] = set()
    for section in mapping["sections"]:
        xml_name = section.get("xml")
        if not isinstance(xml_name, str) or not xml_name.strip():
            raise ValueError(f"section '{section.get('heading') or '?'}' is missing xml mapping")
        if xml_name not in seen:
            ordered_xml_names.append(xml_name)
            seen.add(xml_name)
    for xml_name in mapping["xml_files"]:
        if isinstance(xml_name, str) and xml_name not in seen:
            ordered_xml_names.append(xml_name)
            seen.add(xml_name)

    diagrams = []
    for xml_name in ordered_xml_names:
        xml_path = xml_dir / xml_name
        if not xml_path.is_file():
            raise ValueError(f"xml file not found: {xml_path}")
        ir = extract_ir(xml_path)
        diagrams.append({"xml": xml_name, "mermaid": render_mermaid(ir)})
    return {"diagrams": diagrams}


def main() -> int:
    try:
        args = parse_args()
        raw_json = sys.stdin.read() if args.map_json == "-" else Path(args.map_json).read_text(encoding="utf-8")
        payload = build_diagram_payload(json.loads(raw_json), args.xml_dir)
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.out == "-":
            sys.stdout.write(rendered)
        else:
            Path(args.out).write_text(rendered, encoding="utf-8")
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
