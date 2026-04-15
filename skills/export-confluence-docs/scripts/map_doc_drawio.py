#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MERMAID_FENCE = "```mermaid"
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$", re.MULTILINE)
PAGE_ID_RE = re.compile(r'^confluence_page_id:\s*"?(?P<value>[^"\n]+)"?\s*$', re.MULTILINE)
DRAWIO_PLACEHOLDER_RE = re.compile(
    r'<!--\s*confluence-drawio\b(?P<attrs>.*?)-->',
    re.IGNORECASE | re.DOTALL,
)
ATTR_RE = re.compile(r'([a-zA-Z0-9_:-]+)="([^"]*)"')
DEFAULT_TEMP_ROOT = Path("/tmp/export-confluence-docs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Map a Markdown document to exported draw.io XML files.")
    parser.add_argument("--doc", required=True, help="Path to the Markdown document.")
    parser.add_argument(
        "--xml-dir",
        help="Explicit directory containing XML files. Defaults to /tmp/export-confluence-docs/<doc-stem>--<page_id>.",
    )
    return parser.parse_args()


def split_front_matter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("front matter closing delimiter not found")
    return text[: end + 5], text[end + 5 :]


def parse_sections(body: str) -> list[dict]:
    matches = list(HEADING_RE.finditer(body))
    sections = []
    for index, match in enumerate(matches):
        start = match.start()
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        block = body[start:next_start]
        newline_index = block.find("\n")
        body_text = "" if newline_index < 0 else block[newline_index + 1 :]
        sections.append({"heading": match.group(2).strip(), "level": len(match.group(1)), "body": body_text})
    return sections


def placeholder_attrs(body: str) -> list[dict[str, str]]:
    attrs_list: list[dict[str, str]] = []
    for match in DRAWIO_PLACEHOLDER_RE.finditer(body):
        attrs = {key: value for key, value in ATTR_RE.findall(match.group("attrs"))}
        if attrs:
            attrs_list.append(attrs)
    return attrs_list


def preferred_xml_for_heading(heading: str, xml_files: list[Path]) -> Path | None:
    heading_lower = heading.lower()
    aliases = []
    if "hardware" in heading_lower:
        aliases = ["hwa", "hardware"]
    elif "software" in heading_lower:
        aliases = ["sas", "software"]
    for alias in aliases:
        match = next((path for path in xml_files if alias in path.stem.lower()), None)
        if match is not None:
            return match
    return None


def resolve_xml_dir(doc_path: Path, page_id: str, explicit_xml_dir: str | None) -> Path:
    if explicit_xml_dir:
        return Path(explicit_xml_dir)
    return DEFAULT_TEMP_ROOT / f"{doc_path.stem}--{page_id}"


def main() -> int:
    try:
        args = parse_args()
        doc_path = Path(args.doc)
        text = doc_path.read_text(encoding="utf-8")
        front_matter, body = split_front_matter(text)
        page_id_match = PAGE_ID_RE.search(front_matter)
        if not page_id_match:
            raise ValueError("confluence_page_id not found")
        page_id = page_id_match.group("value").strip()
        xml_dir = resolve_xml_dir(doc_path, page_id, args.xml_dir)
        if not xml_dir.is_dir():
            raise ValueError(f"xml directory not found: {xml_dir}")
        xml_files = sorted(path for path in xml_dir.glob("*.xml") if path.is_file())
        xml_by_name = {path.name: path for path in xml_files}
        xml_by_stem = {path.stem.casefold(): path for path in xml_files}
        sections = parse_sections(body)
        drawio_sections = []
        remaining_xml = xml_files.copy()

        for section in sections:
            attrs_list = placeholder_attrs(section["body"])
            if attrs_list:
                if len(attrs_list) > 1:
                    raise ValueError(f"multiple draw.io placeholders found in section '{section['heading']}'")
                attrs = attrs_list[0]
                chosen = None
                diagram_slug = attrs.get("diagram_slug", "").casefold()
                diagram_name = attrs.get("diagram", "").casefold()
                if diagram_slug and diagram_slug in xml_by_stem:
                    chosen = xml_by_stem[diagram_slug]
                elif diagram_name and diagram_name in xml_by_stem:
                    chosen = xml_by_stem[diagram_name]
                if chosen is None:
                    matches = [
                        path for path in remaining_xml
                        if diagram_slug and path.stem.casefold() == diagram_slug
                        or diagram_name and path.stem.casefold() == diagram_name
                    ]
                    if len(matches) == 1:
                        chosen = matches[0]
                if chosen is not None and chosen in remaining_xml:
                    remaining_xml.remove(chosen)
                drawio_sections.append(
                    {
                        "heading": section["heading"],
                        "xml": chosen.name if chosen else None,
                        "mode": "placeholder",
                        "diagram": attrs.get("diagram", ""),
                        "diagram_slug": attrs.get("diagram_slug", ""),
                    }
                )

        if not drawio_sections:
            mermaid_sections = [section for section in sections if MERMAID_FENCE in section["body"].lower()]
            if mermaid_sections and len(mermaid_sections) == len(xml_files):
                remaining = xml_files.copy()
                for section in mermaid_sections:
                    preferred = preferred_xml_for_heading(section["heading"], remaining)
                    chosen = preferred or remaining[0]
                    remaining.remove(chosen)
                    drawio_sections.append(
                        {
                            "heading": section["heading"],
                            "xml": chosen.name,
                            "mode": "mermaid",
                        }
                    )

        unresolved = [item for item in drawio_sections if item["xml"] is None]
        if unresolved:
            if len(drawio_sections) != len(xml_files):
                raise ValueError("draw.io section count does not match xml file count")
            unused_names = [
                path.name
                for path in xml_files
                if path.name not in {item["xml"] for item in drawio_sections if item["xml"]}
            ]
            for item, xml_name in zip(unresolved, unused_names):
                item["xml"] = xml_name

        payload = {
            "document": {
                "markdown_path": str(doc_path),
                "page_id": page_id,
                "xml_dir": str(xml_dir),
            },
            "sections": drawio_sections,
            "xml_files": [path.name for path in xml_files],
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
