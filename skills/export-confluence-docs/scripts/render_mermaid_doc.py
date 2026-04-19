#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from doc_mapping_utils import (
    DRAWIO_PLACEHOLDER_RE,
    RENDERED_BLOCK_RE,
    extract_section_marker,
    format_rendered_marker,
    match_marker_to_xml,
    parse_sections,
    split_front_matter,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render Mermaid diagrams back into a Markdown document.")
    parser.add_argument("--doc", required=True, help="Path to the Markdown document.")
    parser.add_argument("--diagram-json", required=True, help="JSON file path or - for stdin.")
    parser.add_argument("--check", action="store_true", help="Do not write files; exit with code 2 if changed.")
    parser.add_argument("--stdout", action="store_true", help="Print the rendered Markdown to stdout.")
    return parser.parse_args()

def load_diagrams(raw: dict) -> dict[str, dict]:
    diagrams = raw.get("diagrams")
    if not isinstance(diagrams, list):
        raise ValueError("diagram JSON must contain a diagrams array")
    by_xml = {}
    for item in diagrams:
        if not isinstance(item, dict):
            raise ValueError("each diagram entry must be an object")
        xml_name = item.get("xml")
        mermaid = item.get("mermaid")
        if not isinstance(xml_name, str) or not xml_name.strip():
            raise ValueError("each diagram entry must include xml")
        if not isinstance(mermaid, str) or not mermaid.strip():
            raise ValueError("each diagram entry must include mermaid")
        by_xml[xml_name] = item
    return by_xml

def render_block(attrs: dict[str, str], xml_name: str, mermaid: str) -> str:
    marker = format_rendered_marker(attrs, xml_name)
    return f"{marker}\n```mermaid\n{mermaid.rstrip()}\n```\n"


def strip_drawio_placeholder(body: str) -> str:
    stripped = DRAWIO_PLACEHOLDER_RE.sub("", body)
    return stripped.lstrip("\n")


def replace_diagram_block(section: dict, diagrams_by_xml: dict[str, dict], pending_xml_names: list[str]) -> str:
    try:
        marker_type, attrs = extract_section_marker(section["body"])
    except ValueError as exc:
        raise ValueError(f"{exc} in section '{section['heading']}'") from exc

    if marker_type is None or attrs is None:
        return section["body"]

    xml_name = match_marker_to_xml(attrs, pending_xml_names)
    if xml_name is None:
        xml_name = match_marker_to_xml(attrs, list(diagrams_by_xml.keys()))
    if xml_name is None:
        diagram_name = attrs.get("diagram") or attrs.get("diagram_slug") or "unknown"
        raise ValueError(f"no diagram payload available for section '{section['heading']}' ({diagram_name})")

    diagram = diagrams_by_xml.get(xml_name)
    if diagram is None:
        raise ValueError(f"diagram payload is missing xml '{xml_name}'")
    if xml_name in pending_xml_names:
        pending_xml_names.remove(xml_name)

    replacement = render_block(attrs, xml_name, diagram["mermaid"])
    if marker_type == "placeholder":
        match = DRAWIO_PLACEHOLDER_RE.search(section["body"])
        if match is None:
            raise ValueError(f"draw.io placeholder not found in section '{section['heading']}'")
        return section["body"][: match.start()] + replacement + section["body"][match.end() :]

    match = RENDERED_BLOCK_RE.search(section["body"])
    if match is None:
        raise ValueError(f"rendered draw.io block not found in section '{section['heading']}'")
    return section["body"][: match.start()] + replacement + section["body"][match.end() :]


def render_document(doc_path: Path, diagrams_by_xml: dict[str, dict]) -> str:
    original = doc_path.read_text(encoding="utf-8")
    front_matter, body = split_front_matter(original)
    sections = parse_sections(body)
    preamble = body[: body.find(sections[0]["heading_line"])] if sections else body
    pending_xml_names = list(diagrams_by_xml.keys())

    rendered = front_matter + preamble
    for section in sections:
        rendered_body = replace_diagram_block(section, diagrams_by_xml, pending_xml_names)
        rendered += section["heading_line"] + "\n" + rendered_body

    if pending_xml_names:
        raise ValueError(f"unused diagram payloads: {', '.join(pending_xml_names)}")
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def main() -> int:
    try:
        args = parse_args()
        doc_path = Path(args.doc)
        raw_json = sys.stdin.read() if args.diagram_json == "-" else Path(args.diagram_json).read_text(encoding="utf-8")
        diagrams_by_xml = load_diagrams(json.loads(raw_json))
        original = doc_path.read_text(encoding="utf-8")
        rendered = render_document(doc_path, diagrams_by_xml)

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
