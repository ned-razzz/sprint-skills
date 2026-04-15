#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$", re.MULTILINE)
MERMAID_FENCE = "```mermaid"
DRAWIO_PLACEHOLDER_RE = re.compile(r'<!--\s*confluence-drawio\b.*?-->\s*', re.IGNORECASE | re.DOTALL)


def preferred_xml_for_heading(heading: str, pending_xml_names: list[str]) -> str | None:
    heading_lower = heading.lower()
    aliases = []
    if "hardware" in heading_lower:
        aliases = ["hwa", "hardware"]
    elif "software" in heading_lower:
        aliases = ["sas", "software"]
    for alias in aliases:
        match = next((name for name in pending_xml_names if alias in Path(name).stem.lower()), None)
        if match is not None:
            return match
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render Mermaid diagrams back into a Markdown document.")
    parser.add_argument("--doc", required=True, help="Path to the Markdown document.")
    parser.add_argument("--diagram-json", required=True, help="JSON file path or - for stdin.")
    parser.add_argument("--check", action="store_true", help="Do not write files; exit with code 2 if changed.")
    parser.add_argument("--stdout", action="store_true", help="Print the rendered Markdown to stdout.")
    return parser.parse_args()


def split_front_matter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("front matter closing delimiter not found")
    return text[: end + 5], text[end + 5 :]


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


def parse_sections(body: str) -> list[dict]:
    matches = list(HEADING_RE.finditer(body))
    sections = []
    for index, match in enumerate(matches):
        start = match.start()
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        block = body[start:next_start]
        newline_index = block.find("\n")
        heading_line = block if newline_index < 0 else block[:newline_index]
        section_body = "" if newline_index < 0 else block[newline_index + 1 :]
        sections.append({"heading_line": heading_line, "heading": match.group(2).strip(), "body": section_body})
    return sections


def section_xml_hint(body: str, pending_xml_names: list[str]) -> str | None:
    placeholder = DRAWIO_PLACEHOLDER_RE.search(body)
    if placeholder:
        for xml_name in pending_xml_names:
            if Path(xml_name).stem.lower() in placeholder.group(0).lower():
                return xml_name
    for xml_name in pending_xml_names:
        if Path(xml_name).stem.lower() in body.lower():
            return xml_name
    return None


def strip_drawio_placeholder(body: str) -> str:
    stripped = DRAWIO_PLACEHOLDER_RE.sub("", body)
    return stripped.lstrip("\n")


def main() -> int:
    try:
        args = parse_args()
        doc_path = Path(args.doc)
        original = doc_path.read_text(encoding="utf-8")
        front_matter, body = split_front_matter(original)
        sections = parse_sections(body)
        preamble = body[: body.find(sections[0]["heading_line"])] if sections else body
        raw_json = sys.stdin.read() if args.diagram_json == "-" else Path(args.diagram_json).read_text(encoding="utf-8")
        diagrams_by_xml = load_diagrams(json.loads(raw_json))

        pending_xml_names = list(diagrams_by_xml.keys())
        for section in sections:
            lowered = section["body"].lower()
            if "confluence-drawio" not in lowered and MERMAID_FENCE not in lowered:
                continue
            xml_name = section_xml_hint(section["body"], pending_xml_names)
            if xml_name is None:
                xml_name = preferred_xml_for_heading(section["heading"], pending_xml_names)
            if xml_name is None and pending_xml_names:
                xml_name = pending_xml_names[0]
            if xml_name is None:
                raise ValueError(f"no diagram payload available for section '{section['heading']}'")
            diagram = diagrams_by_xml[xml_name]
            section["body"] = f"```mermaid\n{diagram['mermaid'].rstrip()}\n```\n"
            pending_xml_names.remove(xml_name)
            if "confluence-drawio" in lowered:
                section["body"] = section["body"]
            else:
                section["body"] = section["body"]

        rendered = front_matter + preamble
        for section in sections:
            body_text = strip_drawio_placeholder(section["body"])
            rendered += section["heading_line"] + "\n" + body_text
        if not rendered.endswith("\n"):
            rendered += "\n"

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
