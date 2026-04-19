#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

DEFAULT_TEMP_ROOT = Path("/tmp/export-confluence-docs")

from doc_mapping_utils import (
    PAGE_ID_RE,
    extract_section_marker,
    match_marker_to_xml,
    parse_sections,
    placeholder_attrs,
    split_front_matter,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Map a Markdown document to exported draw.io XML files.")
    parser.add_argument("--doc", required=True, help="Path to the Markdown document.")
    parser.add_argument(
        "--xml-dir",
        help="Explicit directory containing XML files. Defaults to /tmp/export-confluence-docs/<doc-stem>--<page_id>.",
    )
    return parser.parse_args()

def resolve_xml_dir(doc_path: Path, page_id: str, explicit_xml_dir: str | None) -> Path:
    if explicit_xml_dir:
        return Path(explicit_xml_dir)
    return DEFAULT_TEMP_ROOT / f"{doc_path.stem}--{page_id}"


def build_mapping(doc_path: Path, explicit_xml_dir: str | None) -> dict:
    text = doc_path.read_text(encoding="utf-8")
    front_matter, body = split_front_matter(text)
    page_id_match = PAGE_ID_RE.search(front_matter)
    if not page_id_match:
        raise ValueError("confluence_page_id not found")
    page_id = page_id_match.group("value").strip()
    xml_dir = resolve_xml_dir(doc_path, page_id, explicit_xml_dir)
    if not xml_dir.is_dir():
        raise ValueError(f"xml directory not found: {xml_dir}")

    xml_files = sorted(path.name for path in xml_dir.glob("*.xml") if path.is_file())
    sections = parse_sections(body)
    mapped_sections: list[dict[str, str]] = []
    used_xml_names: set[str] = set()

    for section in sections:
        try:
            marker_type, attrs = extract_section_marker(section["body"])
        except ValueError as exc:
            raise ValueError(f"{exc} in section '{section['heading']}'") from exc
        if marker_type is None or attrs is None:
            continue

        xml_name = match_marker_to_xml(attrs, xml_files)
        if xml_name is None:
            diagram_name = attrs.get("diagram") or attrs.get("diagram_slug") or "unknown"
            raise ValueError(
                f"no xml mapping found for section '{section['heading']}' placeholder '{diagram_name}'"
            )
        if xml_name in used_xml_names:
            raise ValueError(f"xml '{xml_name}' is mapped more than once")

        used_xml_names.add(xml_name)
        mapped_sections.append(
            {
                "heading": section["heading"],
                "xml": xml_name,
                "mode": marker_type,
                "diagram": attrs.get("diagram", ""),
                "diagram_slug": attrs.get("diagram_slug", ""),
            }
        )

    unused_xml_names = [name for name in xml_files if name not in used_xml_names]
    if unused_xml_names:
        raise ValueError(f"unmapped xml files: {', '.join(unused_xml_names)}")

    return {
        "document": {
            "markdown_path": str(doc_path),
            "page_id": page_id,
            "xml_dir": str(xml_dir),
        },
        "sections": mapped_sections,
        "xml_files": xml_files,
    }


def main() -> int:
    try:
        args = parse_args()
        payload = build_mapping(Path(args.doc), args.xml_dir)
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
