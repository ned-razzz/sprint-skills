#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TAG_RE = re.compile(r"<[^>]+>")
DECORATIVE_TEXT_LABELS = {"ui", "server", "device"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract a deterministic IR from a draw.io XML file.")
    parser.add_argument("--xml", required=True, help="Path to the draw.io XML file.")
    return parser.parse_args()


def sanitize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = TAG_RE.sub(" ", value)
    return " ".join(value.split())


def parse_float(value: str | None) -> float:
    try:
        return float(value or "0")
    except ValueError:
        return 0.0


def geometry_from_cell(cell: ET.Element) -> dict[str, float]:
    geometry = cell.find("mxGeometry")
    if geometry is None:
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    return {
        "x": parse_float(geometry.attrib.get("x")),
        "y": parse_float(geometry.attrib.get("y")),
        "width": parse_float(geometry.attrib.get("width")),
        "height": parse_float(geometry.attrib.get("height")),
    }


def classify_node(style: str, label: str) -> str:
    lowered = label.lower()
    if "text;" in style:
        return "text"
    if "shape=cylinder3" in style:
        return "database"
    if "ellipse;" in style:
        return "peripheral"
    if "rounded=1" in style:
        if "service" in lowered:
            return "service"
        if "controller" in lowered:
            return "controller"
        return "component"
    if "device" in lowered:
        return "device"
    if "server" in lowered:
        return "server"
    if "kit" in lowered:
        return "group-box"
    return "box"


def contains(outer: dict[str, float], inner: dict[str, float]) -> bool:
    cx = inner["x"] + (inner["width"] / 2.0)
    cy = inner["y"] + (inner["height"] / 2.0)
    return (
        outer["x"] <= cx <= outer["x"] + outer["width"]
        and outer["y"] <= cy <= outer["y"] + outer["height"]
    )


def load_model_root(xml_path: Path) -> ET.Element:
    root = ET.parse(xml_path).getroot()
    if root.tag != "mxGraphModel":
        model = root.find(".//mxGraphModel")
        if model is None:
            raise ValueError("mxGraphModel not found")
        root = model

    cells_root = root.find("root")
    if cells_root is None:
        raise ValueError("graph root not found")
    return cells_root


def unsupported_edge_reason(
    source: str | None,
    target: str | None,
    meaningful_nodes: dict[str, dict],
    unlabeled_vertices: dict[str, dict],
) -> str:
    if not source or not target:
        return "edge is missing a source or target"
    if source in unlabeled_vertices or target in unlabeled_vertices:
        return "edge references an unlabeled vertex"
    if source not in meaningful_nodes or target not in meaningful_nodes:
        return "edge references a non-renderable vertex"
    return "unsupported edge"


def load_graph(xml_path: Path) -> tuple[dict[str, dict], list[dict], list[str], dict[str, list[dict]]]:
    cells = list(load_model_root(xml_path))
    edge_label_by_parent: dict[str, list[str]] = {}
    nodes: dict[str, dict] = {}
    unlabeled_vertices: dict[str, dict] = {}
    edges: list[dict] = []
    connected_unlabeled_vertex_ids: set[str] = set()
    unsupported_edges: list[dict] = []

    for cell in cells:
        if cell.attrib.get("vertex") != "1":
            continue
        style = cell.attrib.get("style", "")
        label = sanitize_text(cell.attrib.get("value", ""))
        if "edgeLabel" in style:
            if label:
                edge_label_by_parent.setdefault(cell.attrib.get("parent", ""), []).append(label)
            continue
        node = {
            "id": cell.attrib["id"],
            "label": label,
            "kind": classify_node(style, label),
            "style": style,
            "geometry": geometry_from_cell(cell),
        }
        if label:
            nodes[cell.attrib["id"]] = node
        else:
            unlabeled_vertices[cell.attrib["id"]] = node

    for cell in cells:
        if cell.attrib.get("edge") != "1":
            continue
        source = cell.attrib.get("source")
        target = cell.attrib.get("target")
        if not source or not target or source not in nodes or target not in nodes:
            if source in unlabeled_vertices:
                connected_unlabeled_vertex_ids.add(source)
            if target in unlabeled_vertices:
                connected_unlabeled_vertex_ids.add(target)
            unsupported_edges.append(
                {
                    "id": cell.attrib.get("id", ""),
                    "source": source,
                    "target": target,
                    "reason": unsupported_edge_reason(source, target, nodes, unlabeled_vertices),
                }
            )
            continue
        raw_label = sanitize_text(cell.attrib.get("value", ""))
        child_labels = edge_label_by_parent.get(cell.attrib.get("id", ""), [])
        label = raw_label or " / ".join(child_labels)
        edges.append(
            {
                "id": cell.attrib.get("id", ""),
                "source": source,
                "target": target,
                "label": label,
            }
        )

    decorative_labels = [
        node["label"]
        for node in nodes.values()
        if node["kind"] == "text" and node["label"].lower() in DECORATIVE_TEXT_LABELS
    ]
    issues = {
        "connected_unlabeled_vertices": sorted(
            (unlabeled_vertices[node_id] for node_id in connected_unlabeled_vertex_ids),
            key=lambda item: (item["geometry"]["y"], item["geometry"]["x"], item["id"]),
        ),
        "unsupported_edges": sorted(
            unsupported_edges,
            key=lambda item: (item.get("source") or "", item.get("target") or "", item.get("id") or ""),
        ),
    }
    return nodes, edges, decorative_labels, issues


def compute_containers(nodes: dict[str, dict], edges: list[dict]) -> list[dict]:
    container_kinds = {"group-box", "server", "box", "component"}
    incident_node_ids = {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
    candidates = []
    for node in nodes.values():
        geometry = node["geometry"]
        if node["kind"] not in container_kinds:
            continue
        if node["id"] in incident_node_ids:
            continue
        if geometry["width"] >= 220 and geometry["height"] >= 150:
            outer_area = geometry["width"] * geometry["height"]
            child_ids = [
                other["id"]
                for other in nodes.values()
                if other["id"] != node["id"]
                and other["kind"] != "text"
                and (other["geometry"]["width"] * other["geometry"]["height"]) < outer_area
                and contains(geometry, other["geometry"])
            ]
            if len(child_ids) >= 2:
                candidates.append(
                    {
                        "id": node["id"],
                        "label": node["label"],
                        "children": sorted(child_ids),
                        "geometry": geometry,
                    }
                )
    return sorted(candidates, key=lambda item: (item["geometry"]["y"], item["geometry"]["x"], item["label"]))


def extract_ir(xml_path: Path) -> dict:
    nodes, edges, decorative_labels, issues = load_graph(xml_path)
    containers = compute_containers(nodes, edges)
    return {
        "document": {
            "xml_path": str(xml_path),
            "xml_name": xml_path.name,
            "diagram_name": xml_path.stem,
        },
        "diagram": {
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
        "nodes": sorted(nodes.values(), key=lambda item: (item["geometry"]["y"], item["geometry"]["x"], item["label"])),
        "edges": sorted(
            edges,
            key=lambda item: (
                nodes[item["source"]]["geometry"]["y"],
                nodes[item["source"]]["geometry"]["x"],
                nodes[item["target"]]["geometry"]["y"],
                nodes[item["target"]]["geometry"]["x"],
                item["label"],
            ),
        ),
        "containers": containers,
        "hints": {
            "diagram_candidates": ["flowchart"],
            "decorative_labels": decorative_labels,
            "protocol_labels": sorted(set(label for label in (edge["label"] for edge in edges) if label)),
        },
        "issues": issues,
    }


def main() -> int:
    try:
        args = parse_args()
        xml_path = Path(args.xml)
        payload = extract_ir(xml_path)
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
