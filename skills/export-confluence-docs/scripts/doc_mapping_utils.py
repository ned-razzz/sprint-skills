from __future__ import annotations

import re
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$", re.MULTILINE)
PAGE_ID_RE = re.compile(r'^confluence_page_id:\s*"?(?P<value>[^"\n]+)"?\s*$', re.MULTILINE)
DRAWIO_PLACEHOLDER_RE = re.compile(
    r'<!--\s*confluence-drawio(?!-rendered)\b(?P<attrs>.*?)-->\s*',
    re.IGNORECASE | re.DOTALL,
)
RENDERED_MARKER_RE = re.compile(
    r'<!--\s*confluence-drawio-rendered\b(?P<attrs>.*?)-->\s*',
    re.IGNORECASE | re.DOTALL,
)
RENDERED_BLOCK_RE = re.compile(
    r'<!--\s*confluence-drawio-rendered\b(?P<attrs>.*?)-->\s*```mermaid\n.*?\n```\n?',
    re.IGNORECASE | re.DOTALL,
)
ATTR_RE = re.compile(r'([a-zA-Z0-9_:-]+)="([^"]*)"')


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
        heading_line = block if newline_index < 0 else block[:newline_index]
        section_body = "" if newline_index < 0 else block[newline_index + 1 :]
        sections.append(
            {
                "heading_line": heading_line,
                "heading": match.group(2).strip(),
                "level": len(match.group(1)),
                "body": section_body,
            }
        )
    return sections


def attrs_from_text(text: str) -> dict[str, str]:
    return {key: value for key, value in ATTR_RE.findall(text)}


def placeholder_attrs(text: str) -> list[dict[str, str]]:
    return [attrs_from_text(match.group("attrs")) for match in DRAWIO_PLACEHOLDER_RE.finditer(text)]


def rendered_marker_attrs(text: str) -> list[dict[str, str]]:
    return [attrs_from_text(match.group("attrs")) for match in RENDERED_MARKER_RE.finditer(text)]


def extract_section_marker(section_body: str) -> tuple[str | None, dict[str, str] | None]:
    placeholders = [attrs for attrs in placeholder_attrs(section_body) if attrs]
    rendered = [attrs for attrs in rendered_marker_attrs(section_body) if attrs]
    marker_count = len(placeholders) + len(rendered)
    if marker_count > 1:
        raise ValueError("section contains multiple draw.io markers")
    if placeholders:
        return "placeholder", placeholders[0]
    if rendered:
        return "rendered", rendered[0]
    return None, None


def match_marker_to_xml(attrs: dict[str, str], xml_names: list[str]) -> str | None:
    xml_name = attrs.get("xml", "").strip()
    xml_by_stem = {Path(name).stem.casefold(): name for name in xml_names}
    if xml_name:
        return xml_name if xml_name in xml_names else None

    diagram_slug = attrs.get("diagram_slug", "").casefold()
    if diagram_slug and diagram_slug in xml_by_stem:
        return xml_by_stem[diagram_slug]

    diagram_name = attrs.get("diagram", "").casefold()
    if diagram_name and diagram_name in xml_by_stem:
        return xml_by_stem[diagram_name]

    return None


def format_rendered_marker(attrs: dict[str, str], xml_name: str) -> str:
    diagram = attrs.get("diagram", "").replace('"', "&quot;")
    diagram_slug = attrs.get("diagram_slug", "").replace('"', "&quot;")
    owner_page_id = attrs.get("owner_page_id", "").replace('"', "&quot;")
    source = attrs.get("source", "").replace('"', "&quot;")
    xml_safe = xml_name.replace('"', "&quot;")
    return (
        "<!-- confluence-drawio-rendered "
        f'diagram="{diagram}" '
        f'diagram_slug="{diagram_slug}" '
        f'owner_page_id="{owner_page_id}" '
        f'source="{source}" '
        f'xml="{xml_safe}"'
        " -->"
    )
