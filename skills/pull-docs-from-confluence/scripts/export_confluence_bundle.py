#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from lxml import etree

AC_NS = "http://atlassian.com/content"
RI_NS = "http://atlassian.com/resource/identifier"
NSMAP = {"ac": AC_NS, "ri": RI_NS}

SUPPORTED_CONTAINER_TAGS = {
    "div",
    "span",
    "section",
    "article",
    "header",
    "footer",
    "tbody",
    "thead",
    "tfoot",
    "colgroup",
}
SUPPORTED_LAYOUT_TAGS = {"layout", "layout-section", "layout-cell"}
DRAWIO_MACRO_NAMES = {"drawio", "inc-drawio"}
DRAWIO_MEDIA_TYPES = {
    "application/vnd.jgraph.mxfile",
    "application/mxfile",
    "application/vnd.jgraph.drawio",
}
TEMP_ROOT = Path("/tmp/export-confluence-docs")


class ConfigError(Exception):
    pass


class FatalConfluenceError(Exception):
    pass


class PageProcessingError(Exception):
    pass


@dataclass
class Config:
    base_url: str
    titles: list[str]
    output_dir: Path
    space_key: str | None


@dataclass(frozen=True)
class DiagramReference:
    diagram_name: str
    owner_page_id: str | None
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Confluence Markdown plus draw.io XML references for Mermaid conversion.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to a JSON config file, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--temp-root",
        default=str(TEMP_ROOT),
        help="Base temporary directory for exported draw.io XML.",
    )
    return parser.parse_args()


def load_config(config_arg: str) -> Config:
    if config_arg == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(config_arg).read_text(encoding="utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON config: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")

    base_url = data.get("baseUrl")
    titles = data.get("titles")
    output_dir = data.get("outputDir", "./docs")
    space_key = data.get("spaceKey")

    if not isinstance(base_url, str) or not base_url.strip():
        raise ConfigError("baseUrl must be a non-empty string")
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ConfigError("outputDir must be a non-empty string")
    if not isinstance(titles, list) or not titles:
        raise ConfigError("titles must be a non-empty array")
    if not all(isinstance(title, str) and title.strip() for title in titles):
        raise ConfigError("each title must be a non-empty string")
    if space_key is not None and (not isinstance(space_key, str) or not space_key.strip()):
        raise ConfigError("spaceKey must be a non-empty string when provided")

    normalized_base = base_url.rstrip("/")
    parsed = urlparse(normalized_base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("baseUrl must be a valid http(s) URL")

    resolved_output = Path(output_dir).expanduser()
    if not resolved_output.is_absolute():
        resolved_output = Path.cwd() / resolved_output

    return Config(
        base_url=normalized_base,
        titles=[title.strip() for title in titles],
        output_dir=resolved_output,
        space_key=space_key.strip() if isinstance(space_key, str) else None,
    )


def require_credentials() -> tuple[str, str]:
    email = os.environ.get("CONFLUENCE_EMAIL", "").strip()
    token = os.environ.get("CONFLUENCE_API_TOKEN", "").strip()
    if not email or not token:
        raise ConfigError("CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN must be set")
    return email, token


def cql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def cql_string_literal(value: str) -> str:
    return f'"{cql_escape(value)}"'


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug


def mermaid_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "diagram"


def parse_confluence_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def local_name(node: etree._Element) -> str:
    return etree.QName(node).localname


def namespace_uri(node: etree._Element) -> str | None:
    return etree.QName(node).namespace


def namespaced_attr(node: etree._Element, namespace: str, attr_name: str) -> str | None:
    return node.get(f"{{{namespace}}}{attr_name}")


def normalize_adf_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.casefold())


def adf_haystack(node: etree._Element) -> str:
    haystack_parts: list[str] = []
    extension_key = node.get("extension-key")
    if extension_key:
        haystack_parts.append(extension_key)
    for attribute in node.findall(".//ac:adf-attribute", namespaces=NSMAP):
        key = attribute.get("key")
        if key:
            haystack_parts.append(key)
        text = "".join(attribute.itertext()).strip()
        if text:
            haystack_parts.append(text)
    for parameter in node.findall(".//ac:adf-parameter", namespaces=NSMAP):
        key = parameter.get("key")
        if key:
            haystack_parts.append(key)
        text = "".join(parameter.itertext()).strip()
        if text:
            haystack_parts.append(text)
    return " ".join(haystack_parts).casefold()


def site_root(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def build_page_url(base_url: str, page: dict[str, Any]) -> str:
    links = page.get("_links") or {}
    webui = links.get("webui")
    if isinstance(webui, str) and webui:
        return urljoin(site_root(base_url), webui)
    return base_url


def debug_enabled() -> bool:
    value = os.environ.get("CONFLUENCE_DEBUG", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def debug_log(message: str) -> None:
    if debug_enabled():
        print(f"[confluence-debug] {message}", file=sys.stderr)


def choose_page(title: str, pages: list[dict[str, Any]]) -> dict[str, Any]:
    exact_matches = [
        page
        for page in pages
        if isinstance(page.get("title"), str) and page["title"].strip() == title.strip()
    ]
    if not exact_matches:
        raise PageProcessingError(f"no exact title match found for '{title}'")
    return max(
        exact_matches,
        key=lambda page: (
            parse_confluence_datetime((page.get("version") or {}).get("when")),
            int((page.get("version") or {}).get("number") or 0),
        ),
    )


def page_frontmatter(
    title: str,
    page_id: str,
    version: int,
    source: str,
    markdown_body: str,
) -> str:
    frontmatter = (
        "---\n"
        f'title: {json.dumps(title, ensure_ascii=False)}\n'
        f'confluence_page_id: {json.dumps(page_id)}\n'
        f"version: {version}\n"
        f'source: {json.dumps(source)}\n'
        "---\n\n"
    )
    return frontmatter + markdown_body.lstrip()


def page_directory_name(title: str, page_id: str) -> str:
    base = slugify(title) or f"page-{page_id}"
    return f"{base}--{page_id}"


def markdown_output_name(title: str, page_id: str) -> str:
    return slugify(title) or f"page-{page_id}"


def sanitize_output_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "-", value).strip().strip(".")
    value = re.sub(r"\s+", " ", value)
    return value or "diagram"


def normalized_xml_filename(diagram_name: str, attachment_title: str) -> str:
    diagram_slug = mermaid_slug(diagram_name)
    if diagram_slug and diagram_slug != "diagram":
        return f"{diagram_slug}.xml"
    title = sanitize_output_name(Path(attachment_title).stem)
    return f"{title}.xml"


class ConfluenceClient:
    def __init__(self, base_url: str, email: str, token: str) -> None:
        self.base_url = base_url
        self.session = requests.Session()
        self.session.auth = (email, token)
        self.session.headers.update({"Accept": "application/json"})

    def _request(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        url = f"{self.base_url}{path}"
        prepared = requests.Request("GET", url, params=params).prepare()
        debug_log(f"GET {prepared.url}")
        try:
            response = self.session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            raise PageProcessingError(f"request failed for {url}: {exc}") from exc
        if response.status_code in {401, 403}:
            raise FatalConfluenceError(
                f"authentication failed with status {response.status_code} for {url}"
            )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            details = response.text.strip()
            if details:
                raise PageProcessingError(
                    f"request failed for {url}: {exc}; response body: {details}"
                ) from exc
            raise PageProcessingError(f"request failed for {url}: {exc}") from exc
        return response

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._request(path, params=params)
        try:
            data = response.json()
        except ValueError as exc:
            raise PageProcessingError(f"invalid JSON response for {response.url}: {exc}") from exc
        if not isinstance(data, dict):
            raise PageProcessingError(f"unexpected response payload for {response.url}")
        return data

    def search_pages(self, title: str, space_key: str | None) -> list[dict[str, Any]]:
        title_clause = f"title = {cql_string_literal(title)}"
        cql_parts = ["type = page", title_clause]
        if space_key:
            cql_parts.append(f"space = {cql_string_literal(space_key)}")
        payload = self._get_json(
            "/rest/api/content/search",
            params={"cql": " AND ".join(cql_parts), "limit": 100},
        )
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise PageProcessingError("unexpected search response: results is not a list")
        return [item for item in results if isinstance(item, dict)]

    def fetch_page(self, page_id: str) -> dict[str, Any]:
        return self._get_json(
            f"/rest/api/content/{page_id}",
            params={"expand": "body.storage,version"},
        )

    def list_attachments(self, page_id: str) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        start = 0
        limit = 200
        while True:
            payload = self._get_json(
                f"/rest/api/content/{page_id}/child/attachment",
                params={"limit": limit, "start": start},
            )
            page_results = payload.get("results", [])
            if not isinstance(page_results, list):
                raise PageProcessingError(
                    f"unexpected attachment response for page {page_id}: results is not a list"
                )
            attachments.extend(item for item in page_results if isinstance(item, dict))
            if len(page_results) < limit:
                return attachments
            start += limit

    def download_attachment(self, download_path: str) -> bytes:
        response = self._request(download_path)
        return response.content


class DrawioReferenceExtractor:
    def extract(self, storage_value: str) -> list[DiagramReference]:
        wrapped = f'<root xmlns:ac="{AC_NS}" xmlns:ri="{RI_NS}">{storage_value}</root>'
        try:
            root = etree.fromstring(wrapped.encode("utf-8"))
        except etree.XMLSyntaxError as exc:
            raise PageProcessingError(f"invalid body.storage XML: {exc}") from exc

        references = self._extract_structured_macro_references(root)
        references.extend(self._extract_adf_extension_references(root))

        deduped: list[DiagramReference] = []
        seen: set[tuple[str, str, str]] = set()
        for reference in references:
            key = (
                reference.diagram_name.casefold(),
                (reference.owner_page_id or "").strip(),
                reference.source,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(reference)
        return deduped

    def _extract_structured_macro_references(
        self, root: etree._Element
    ) -> list[DiagramReference]:
        references: list[DiagramReference] = []
        for macro in root.findall(".//ac:structured-macro", namespaces=NSMAP):
            macro_name = (namespaced_attr(macro, AC_NS, "name") or "").strip()
            if macro_name not in DRAWIO_MACRO_NAMES:
                continue
            parameters = self._structured_macro_parameters(macro)
            diagram_name = (
                parameters.get("diagramName") or parameters.get("diagramDisplayName") or ""
            ).strip()
            if not diagram_name:
                continue
            owner_page_id = (
                parameters.get("pageId")
                or parameters.get("contentId")
                or parameters.get("imgPageId")
                or None
            )
            references.append(
                DiagramReference(
                    diagram_name=diagram_name,
                    owner_page_id=owner_page_id.strip() if isinstance(owner_page_id, str) else None,
                    source=f"structured-macro:{macro_name}",
                )
            )
        return references

    def _structured_macro_parameters(self, macro: etree._Element) -> dict[str, str]:
        parameters: dict[str, str] = {}
        for parameter in macro.findall("ac:parameter", namespaces=NSMAP):
            name = (namespaced_attr(parameter, AC_NS, "name") or "").strip()
            if not name:
                continue
            parameters[name] = "".join(parameter.itertext()).strip()
        return parameters

    def _extract_adf_extension_references(
        self, root: etree._Element
    ) -> list[DiagramReference]:
        references: list[DiagramReference] = []
        for node in self._iter_adf_extension_nodes(root):
            if not self._is_drawio_adf_extension(node):
                continue
            parameters = self._collect_adf_parameters(node)
            diagram_names = self._candidate_values(parameters, {"diagramName", "diagramDisplayName"})
            owner_page_ids = self._candidate_values(parameters, {"pageId", "contentId", "imgPageId"})
            owner_page_id = next((value for value in owner_page_ids if value.isdigit()), None)
            for diagram_name in diagram_names:
                references.append(
                    DiagramReference(
                        diagram_name=diagram_name,
                        owner_page_id=owner_page_id,
                        source="adf-extension",
                    )
                )
        return references

    def _iter_adf_extension_nodes(self, root: etree._Element) -> list[etree._Element]:
        nodes: list[etree._Element] = []
        seen: set[int] = set()
        for node in root.findall(".//ac:adf-node", namespaces=NSMAP):
            if node.get("type") != "extension":
                continue
            marker = id(node)
            if marker in seen:
                continue
            seen.add(marker)
            nodes.append(node)
        for node in root.findall(".//ac:adf-extension", namespaces=NSMAP):
            marker = id(node)
            if marker in seen:
                continue
            seen.add(marker)
            nodes.append(node)
        return nodes

    def _is_drawio_adf_extension(self, node: etree._Element) -> bool:
        haystack = adf_haystack(node)
        return any(marker in haystack for marker in {"drawio", "draw.io", "inc-drawio"})

    def _collect_adf_parameters(self, node: etree._Element) -> dict[str, list[str]]:
        collected: dict[str, list[str]] = {}
        for parameter in node.findall(".//ac:adf-parameter", namespaces=NSMAP):
            key = (parameter.get("key") or "").strip()
            text = "".join(parameter.itertext()).strip()
            if not key or not text:
                continue
            collected.setdefault(normalize_adf_key(key), []).append(text)
        return collected

    def _candidate_values(
        self, parameters: dict[str, list[str]], keys: set[str]
    ) -> list[str]:
        values: list[str] = []
        normalized_keys = {normalize_adf_key(key) for key in keys}
        for key, items in parameters.items():
            if normalize_adf_key(key) not in normalized_keys:
                continue
            values.extend(item for item in items if item.strip())
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped


def media_type_of_attachment(attachment: dict[str, Any]) -> str:
    metadata = attachment.get("metadata") or {}
    media_type = metadata.get("mediaType")
    if isinstance(media_type, str) and media_type:
        return media_type
    extensions = attachment.get("extensions") or {}
    media_type = extensions.get("mediaType")
    return str(media_type or "")


def download_path_of_attachment(attachment: dict[str, Any]) -> str:
    links = attachment.get("_links") or {}
    download = links.get("download")
    return str(download or "")


def build_attachment_download_url(base_url: str, attachment: dict[str, Any]) -> str:
    download_path = download_path_of_attachment(attachment)
    if not download_path:
        return ""
    return urljoin(site_root(base_url), download_path)


def attachment_match_score(attachment: dict[str, Any], diagram_name: str) -> int:
    title = str(attachment.get("title") or "").strip()
    if not title:
        return -1
    lower_title = title.casefold()
    lower_diagram = diagram_name.casefold()
    media_type = media_type_of_attachment(attachment).casefold()
    score = 0
    if lower_title == lower_diagram:
        score += 100
    elif lower_title == f"{lower_diagram}.drawio":
        score += 95
    elif lower_title == f"{lower_diagram}.drawio.xml":
        score += 95
    elif lower_title == f"{lower_diagram}.xml":
        score += 90
    elif Path(lower_title).stem == lower_diagram:
        score += 75
    elif lower_diagram in lower_title:
        score += 50
    if media_type in DRAWIO_MEDIA_TYPES:
        score += 20
    elif media_type in {"text/xml", "application/xml"}:
        score += 10
    return score


def find_matching_attachment(
    attachments: list[dict[str, Any]], diagram_name: str
) -> dict[str, Any] | None:
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for attachment in attachments:
        score = attachment_match_score(attachment, diagram_name)
        if score < 0:
            continue
        version = int(((attachment.get("version") or {}).get("number")) or 0)
        candidates.append((score, version, attachment))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def find_fallback_drawio_attachments(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fallback: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for attachment in attachments:
        attachment_id = str(attachment.get("id") or "")
        title = str(attachment.get("title") or "").strip().casefold()
        media_type = media_type_of_attachment(attachment).casefold()
        if not attachment_id or attachment_id in seen_ids:
            continue
        if media_type in DRAWIO_MEDIA_TYPES or title.endswith(".drawio") or title.endswith(".drawio.xml"):
            seen_ids.add(attachment_id)
            fallback.append(attachment)
        elif title.endswith(".xml") and "drawio" in title:
            seen_ids.add(attachment_id)
            fallback.append(attachment)
    return fallback


def ensure_xml_content(content: bytes, attachment_title: str) -> None:
    try:
        etree.fromstring(content)
    except etree.XMLSyntaxError as exc:
        raise PageProcessingError(
            f"downloaded attachment is not valid XML for '{attachment_title}': {exc}"
        ) from exc


class StorageToMarkdownConverter:
    def __init__(self, drawio_references: list[DiagramReference]) -> None:
        self._references_by_key: dict[tuple[str, str | None, str], DiagramReference] = {}
        self._references_by_name: dict[str, list[DiagramReference]] = {}
        for reference in drawio_references:
            key = (
                reference.diagram_name.casefold(),
                reference.owner_page_id,
                reference.source,
            )
            self._references_by_key[key] = reference
            self._references_by_name.setdefault(reference.diagram_name.casefold(), []).append(reference)

    def convert(self, storage_value: str) -> str:
        wrapped = f'<root xmlns:ac="{AC_NS}" xmlns:ri="{RI_NS}">{storage_value}</root>'
        try:
            root = etree.fromstring(wrapped.encode("utf-8"))
        except etree.XMLSyntaxError as exc:
            raise PageProcessingError(f"invalid body.storage XML: {exc}") from exc

        blocks = self._convert_children(root)
        markdown = "\n\n".join(block for block in blocks if block.strip())
        markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
        return f"{markdown}\n" if markdown else ""

    def _convert_children(self, parent: etree._Element) -> list[str]:
        blocks: list[str] = []
        if parent.text and parent.text.strip():
            blocks.append(self._clean_inline_text(parent.text))
        for child in parent:
            blocks.extend(self._convert_node(child))
            if child.tail and child.tail.strip():
                blocks.append(self._clean_inline_text(child.tail))
        return [block for block in blocks if block.strip()]

    def _convert_node(self, node: etree._Element, list_indent: int = 0) -> list[str]:
        name = local_name(node)
        namespace = namespace_uri(node)

        if namespace == AC_NS and name in SUPPORTED_LAYOUT_TAGS:
            return self._convert_children(node)
        if name in SUPPORTED_CONTAINER_TAGS:
            return self._convert_children(node)
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(name[1])
            content = self._render_inline_children(node).strip()
            return [f"{'#' * level} {content}"] if content else []
        if name == "p":
            content = self._render_inline_children(node).strip()
            return [content] if content else []
        if name == "blockquote":
            content = self._render_inline_children(node).strip()
            if not content:
                nested = "\n\n".join(self._convert_children(node))
                content = nested.strip()
            if not content:
                return []
            return ["\n".join(f"> {line}" if line else ">" for line in content.splitlines())]
        if name == "pre":
            code = "".join(node.itertext()).strip("\n")
            return [self._fenced_code_block(code, None)] if code else []
        if name in {"ul", "ol"}:
            return [self._render_list(node, ordered=(name == "ol"), indent=list_indent)]
        if name == "table":
            table = self._render_table(node)
            return [table] if table else []
        if name == "hr":
            return ["---"]
        if namespace == AC_NS and name == "structured-macro":
            macro_name = namespaced_attr(node, AC_NS, "name") or ""
            if macro_name == "code":
                code = self._macro_code_body(node)
                language = self._macro_parameter(node, "language")
                return [self._fenced_code_block(code, language)]
            if macro_name in DRAWIO_MACRO_NAMES:
                return [self._drawio_placeholder_from_macro(node, macro_name)]
            return ["<!-- unsupported macro -->"]
        if (
            namespace == AC_NS
            and name == "adf-node"
            and node.getparent() is not None
            and namespace_uri(node.getparent()) == AC_NS
            and local_name(node.getparent()) == "adf-extension"
        ):
            return []
        if namespace == AC_NS and name in {"adf-node", "adf-extension"}:
            placeholder = self._drawio_placeholder_from_adf(node)
            return [placeholder] if placeholder else []
        if namespace == AC_NS and name in {"plain-text-body", "link-body"}:
            text = "".join(node.itertext()).strip()
            return [text] if text else []
        content = self._render_inline_children(node).strip()
        return [content] if content else []

    def _drawio_placeholder_from_macro(self, node: etree._Element, macro_name: str) -> str:
        parameters = {}
        for parameter in node.findall("ac:parameter", namespaces=NSMAP):
            name = (namespaced_attr(parameter, AC_NS, "name") or "").strip()
            if not name:
                continue
            parameters[name] = "".join(parameter.itertext()).strip()
        diagram_name = (
            parameters.get("diagramName") or parameters.get("diagramDisplayName") or ""
        ).strip()
        owner_page_id = (
            parameters.get("pageId")
            or parameters.get("contentId")
            or parameters.get("imgPageId")
            or None
        )
        if not diagram_name:
            return "<!-- unsupported drawio macro -->"
        return self._format_drawio_placeholder(
            diagram_name=diagram_name,
            owner_page_id=owner_page_id.strip() if isinstance(owner_page_id, str) else None,
            source=f"structured-macro:{macro_name}",
        )

    def _render_list(self, node: etree._Element, ordered: bool, indent: int) -> str:
        lines: list[str] = []
        counter = 1
        for child in node:
            if local_name(child) != "li":
                continue
            marker = f"{counter}. " if ordered else "- "
            prefix = " " * indent
            inline = self._render_list_item_inline(child)
            lines.append(f"{prefix}{marker}{inline}".rstrip())
            nested_blocks = self._render_nested_lists(child, indent + 2)
            if nested_blocks:
                lines.extend(nested_blocks)
            counter += 1
        return "\n".join(line for line in lines if line.strip())

    def _render_list_item_inline(self, node: etree._Element) -> str:
        parts: list[str] = []
        if node.text:
            parts.append(node.text)
        for child in node:
            child_name = local_name(child)
            child_ns = namespace_uri(child)
            if child_name in {"ul", "ol"}:
                continue
            if child_name == "p":
                paragraph = self._render_inline_children(child)
                if paragraph:
                    parts.append(paragraph)
            elif child_ns == AC_NS and child_name == "structured-macro":
                macro_name = namespaced_attr(child, AC_NS, "name") or ""
                if macro_name == "code":
                    parts.append("[code block below]")
                elif macro_name in DRAWIO_MACRO_NAMES:
                    parts.append(self._drawio_placeholder_from_macro(child, macro_name))
                else:
                    parts.append("unsupported macro")
            else:
                parts.append(self._render_inline(child))
            if child.tail:
                parts.append(child.tail)
        return self._clean_inline_text(" ".join(parts))

    def _render_nested_lists(self, node: etree._Element, indent: int) -> list[str]:
        nested: list[str] = []
        for child in node:
            if local_name(child) in {"ul", "ol"}:
                nested.append(self._render_list(child, ordered=(local_name(child) == "ol"), indent=indent))
        return [block for block in nested if block.strip()]

    def _render_table(self, node: etree._Element) -> str:
        rows: list[list[str]] = []
        first_row_has_header = False
        for row in node.xpath(".//*[local-name()='tr']"):
            rendered_row: list[str] = []
            header_flags: list[bool] = []
            for cell in row:
                name = local_name(cell)
                if name not in {"th", "td"}:
                    continue
                rendered_row.append(self._escape_table_cell(self._render_inline_children(cell)))
                header_flags.append(name == "th")
            if rendered_row:
                if not rows:
                    first_row_has_header = any(header_flags)
                rows.append(rendered_row)
        if not rows:
            return ""
        column_count = max(len(row) for row in rows)
        normalized = [row + [""] * (column_count - len(row)) for row in rows]
        header = normalized[0]
        body = normalized[1:]
        separator = ["---"] * column_count
        lines = [f"| {' | '.join(header)} |", f"| {' | '.join(separator)} |"]
        for row in body:
            lines.append(f"| {' | '.join(row)} |")
        return "\n".join(lines)

    def _escape_table_cell(self, value: str) -> str:
        cleaned = self._clean_inline_text(value)
        return cleaned.replace("|", r"\|") if cleaned else ""

    def _render_inline_children(self, node: etree._Element) -> str:
        parts: list[str] = []
        if node.text:
            parts.append(node.text)
        for child in node:
            parts.append(self._render_inline(child))
            if child.tail:
                parts.append(child.tail)
        return self._clean_inline_text("".join(parts))

    def _render_inline(self, node: etree._Element) -> str:
        name = local_name(node)
        namespace = namespace_uri(node)
        if name == "br":
            return "  \n"
        if name in {"strong", "b"}:
            content = self._render_inline_children(node)
            return f"**{content}**" if content else ""
        if name in {"em", "i"}:
            content = self._render_inline_children(node)
            return f"*{content}*" if content else ""
        if name == "code":
            content = "".join(node.itertext()).strip()
            return f"`{content}`" if content else ""
        if name == "a":
            href = node.get("href", "").strip()
            text = self._render_inline_children(node) or href
            return f"[{text}]({href})" if href else text
        if namespace == AC_NS and name == "link":
            return self._render_confluence_link(node)
        if namespace == AC_NS and name in {"plain-text-link-body", "link-body"}:
            return self._render_inline_children(node)
        if (
            namespace == AC_NS
            and name == "adf-node"
            and node.getparent() is not None
            and namespace_uri(node.getparent()) == AC_NS
            and local_name(node.getparent()) == "adf-extension"
        ):
            return ""
        if namespace == AC_NS and name in {"adf-node", "adf-extension"}:
            placeholder = self._drawio_placeholder_from_adf(node)
            return placeholder if placeholder else self._render_inline_children(node)
        if name in SUPPORTED_CONTAINER_TAGS:
            return self._render_inline_children(node)
        return self._render_inline_children(node)

    def _drawio_placeholder_from_adf(self, node: etree._Element) -> str | None:
        haystack = adf_haystack(node)
        if not any(marker in haystack for marker in {"drawio", "draw.io", "inc-drawio"}):
            return None
        parameters: dict[str, list[str]] = {}
        for parameter in node.findall(".//ac:adf-parameter", namespaces=NSMAP):
            key = (parameter.get("key") or "").strip()
            text = "".join(parameter.itertext()).strip()
            if not key or not text:
                continue
            parameters.setdefault(normalize_adf_key(key), []).append(text)
        diagram_name = next(
            iter(parameters.get("diagramname", []) or parameters.get("diagramdisplayname", [])),
            "",
        ).strip()
        owner_page_id = next(
            (
                value
                for value in parameters.get("pageid", [])
                + parameters.get("contentid", [])
                + parameters.get("imgpageid", [])
                if value.isdigit()
            ),
            None,
        )
        if not diagram_name:
            return None
        return self._format_drawio_placeholder(
            diagram_name=diagram_name,
            owner_page_id=owner_page_id,
            source="adf-extension",
        )

    def _format_drawio_placeholder(
        self, diagram_name: str, owner_page_id: str | None, source: str
    ) -> str:
        return (
            "<!-- confluence-drawio "
            f'diagram="{diagram_name.replace(chr(34), "&quot;")}" '
            f'diagram_slug="{mermaid_slug(diagram_name)}" '
            f'owner_page_id="{(owner_page_id or "").replace(chr(34), "&quot;")}" '
            f'source="{source.replace(chr(34), "&quot;")}"'
            " -->"
        )

    def _render_confluence_link(self, node: etree._Element) -> str:
        link_text = self._link_text(node)
        target = self._link_target(node)
        if target:
            return f"[{link_text or target}]({target})"
        return link_text

    def _link_text(self, node: etree._Element) -> str:
        plain = node.find("ac:plain-text-link-body", namespaces=NSMAP)
        if plain is not None:
            return "".join(plain.itertext()).strip()
        rich = node.find("ac:link-body", namespaces=NSMAP)
        if rich is not None:
            return self._render_inline_children(rich).strip()
        page = node.find("ri:page", namespaces=NSMAP)
        if page is not None:
            return (
                namespaced_attr(page, RI_NS, "content-title")
                or namespaced_attr(page, RI_NS, "page-title")
                or ""
            )
        url_node = node.find("ri:url", namespaces=NSMAP)
        if url_node is not None:
            return (namespaced_attr(url_node, RI_NS, "value") or "").strip()
        attachment = node.find("ri:attachment", namespaces=NSMAP)
        if attachment is not None:
            return namespaced_attr(attachment, RI_NS, "filename") or "attachment"
        return self._render_inline_children(node).strip()

    def _link_target(self, node: etree._Element) -> str:
        url_node = node.find("ri:url", namespaces=NSMAP)
        if url_node is not None:
            return (namespaced_attr(url_node, RI_NS, "value") or "").strip()
        return ""

    def _macro_parameter(self, node: etree._Element, name: str) -> str | None:
        for parameter in node.findall("ac:parameter", namespaces=NSMAP):
            if namespaced_attr(parameter, AC_NS, "name") == name:
                value = "".join(parameter.itertext()).strip()
                return value or None
        return None

    def _macro_code_body(self, node: etree._Element) -> str:
        plain = node.find("ac:plain-text-body", namespaces=NSMAP)
        if plain is not None:
            return "".join(plain.itertext()).strip("\n")
        rich = node.find("ac:rich-text-body", namespaces=NSMAP)
        if rich is not None:
            return "\n\n".join(self._convert_children(rich)).strip()
        return ""

    def _fenced_code_block(self, code: str, language: str | None) -> str:
        language_suffix = language.strip() if language else ""
        return f"```{language_suffix}\n{code}\n```".rstrip()

    def _clean_inline_text(self, value: str) -> str:
        value = value.replace("\xa0", " ")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        placeholder = "__CONFLUENCE_BR__"
        value = value.replace("  \n", placeholder)
        value = re.sub(r"[ \t]+", " ", value)
        value = value.replace(placeholder, "  \n")
        return value.strip()


def clean_temp_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def unique_output_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def attachments_for_page(
    client: ConfluenceClient,
    attachment_cache: dict[str, list[dict[str, Any]]],
    page_id: str,
) -> list[dict[str, Any]]:
    cached = attachment_cache.get(page_id)
    if cached is not None:
        return cached
    attachments = client.list_attachments(page_id)
    attachment_cache[page_id] = attachments
    return attachments


def export_drawio_xml(
    client: ConfluenceClient,
    references: list[DiagramReference],
    page_id: str,
    temp_xml_dir: Path,
    attachment_cache: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    saved: list[dict[str, Any]] = []
    warnings: list[str] = []
    processed_attachment_ids: set[str] = set()

    if references:
        for reference in references:
            owner_page_id = reference.owner_page_id or page_id
            attachments = attachments_for_page(client, attachment_cache, owner_page_id)
            attachment = find_matching_attachment(attachments, reference.diagram_name)
            if attachment is None:
                warnings.append(
                    f"diagram attachment not found for '{reference.diagram_name}' "
                    f"(ownerPageId={owner_page_id}, source={reference.source})"
                )
                continue
            attachment_id = str(attachment.get("id") or "")
            if not attachment_id or attachment_id in processed_attachment_ids:
                continue
            download_path = download_path_of_attachment(attachment)
            if not download_path:
                warnings.append(
                    f"attachment download link missing for '{reference.diagram_name}' "
                    f"(ownerPageId={owner_page_id})"
                )
                continue
            filename = normalized_xml_filename(
                diagram_name=reference.diagram_name,
                attachment_title=str(attachment.get("title") or reference.diagram_name),
            )
            output_path = unique_output_path(temp_xml_dir / filename)
            content = client.download_attachment(download_path)
            ensure_xml_content(content, str(attachment.get("title") or reference.diagram_name))
            output_path.write_bytes(content)
            processed_attachment_ids.add(attachment_id)
            saved.append(
                {
                    "diagramName": reference.diagram_name,
                    "diagramSlug": mermaid_slug(reference.diagram_name),
                    "attachmentTitle": str(attachment.get("title") or ""),
                    "attachmentId": attachment_id,
                    "ownerPageId": owner_page_id,
                    "downloadUrl": build_attachment_download_url(client.base_url, attachment),
                    "path": str(output_path),
                    "xml": output_path.name,
                    "source": reference.source,
                }
            )
    else:
        attachments = attachments_for_page(client, attachment_cache, page_id)
        for attachment in find_fallback_drawio_attachments(attachments):
            attachment_id = str(attachment.get("id") or "")
            if not attachment_id or attachment_id in processed_attachment_ids:
                continue
            download_path = download_path_of_attachment(attachment)
            if not download_path:
                continue
            title = str(attachment.get("title") or "diagram")
            output_path = unique_output_path(
                temp_xml_dir / normalized_xml_filename("diagram", title)
            )
            content = client.download_attachment(download_path)
            ensure_xml_content(content, title)
            output_path.write_bytes(content)
            processed_attachment_ids.add(attachment_id)
            saved.append(
                {
                    "diagramName": title,
                    "diagramSlug": mermaid_slug(Path(title).stem),
                    "attachmentTitle": title,
                    "attachmentId": attachment_id,
                    "ownerPageId": page_id,
                    "downloadUrl": build_attachment_download_url(client.base_url, attachment),
                    "path": str(output_path),
                    "xml": output_path.name,
                    "source": "attachment-fallback",
                }
            )
        if not saved:
            warnings.append("no draw.io references or matching draw.io attachments found")
    return saved, warnings


def process_title(
    client: ConfluenceClient,
    extractor: DrawioReferenceExtractor,
    config: Config,
    title: str,
    temp_root: Path,
    attachment_cache: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    pages = client.search_pages(title, config.space_key)
    selected = choose_page(title, pages)
    page_id = str(selected.get("id") or "")
    if not page_id:
        raise PageProcessingError(f"missing page id for '{title}'")

    page = client.fetch_page(page_id)
    storage = (((page.get("body") or {}).get("storage") or {}).get("value")) or ""
    if not isinstance(storage, str):
        raise PageProcessingError(f"missing body.storage for '{title}'")

    references = extractor.extract(storage)
    temp_xml_dir = temp_root / page_directory_name(title, page_id)
    clean_temp_dir(temp_xml_dir)

    converter = StorageToMarkdownConverter(references)
    markdown = converter.convert(storage)
    version_number = int(((page.get("version") or {}).get("number")) or 0)
    source = build_page_url(config.base_url, page)

    output_path = config.output_dir / f"{markdown_output_name(title, page_id)}.md"
    content = page_frontmatter(title, page_id, version_number, source, markdown)
    output_path.write_text(content, encoding="utf-8")

    xml_entries, warnings = export_drawio_xml(
        client=client,
        references=references,
        page_id=page_id,
        temp_xml_dir=temp_xml_dir,
        attachment_cache=attachment_cache,
    )

    if warnings and xml_entries:
        status = "partial"
    elif warnings and not xml_entries and references:
        status = "failed"
    else:
        status = "succeeded"

    return {
        "title": title,
        "status": status,
        "pageId": page_id,
        "version": version_number,
        "source": source,
        "markdownPath": str(output_path),
        "tempXmlDir": str(temp_xml_dir),
        "diagramCount": len(xml_entries),
        "xmlFiles": [entry["xml"] for entry in xml_entries],
        "xmlEntries": xml_entries,
        "warnings": warnings,
    }


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "processed": len(results),
        "succeeded": sum(1 for result in results if result["status"] == "succeeded"),
        "partial": sum(1 for result in results if result["status"] == "partial"),
        "failed": sum(1 for result in results if result["status"] == "failed"),
        "results": results,
    }


def main() -> int:
    try:
        args = parse_args()
        config = load_config(args.config)
        email, token = require_credentials()
    except (ConfigError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    config.output_dir.mkdir(parents=True, exist_ok=True)
    temp_root = Path(args.temp_root).expanduser()
    temp_root.mkdir(parents=True, exist_ok=True)

    client = ConfluenceClient(config.base_url, email, token)
    extractor = DrawioReferenceExtractor()
    attachment_cache: dict[str, list[dict[str, Any]]] = {}
    results: list[dict[str, Any]] = []

    try:
        for title in config.titles:
            try:
                results.append(
                    process_title(
                        client=client,
                        extractor=extractor,
                        config=config,
                        title=title,
                        temp_root=temp_root,
                        attachment_cache=attachment_cache,
                    )
                )
            except PageProcessingError as exc:
                results.append({"title": title, "status": "failed", "reason": str(exc)})
    except FatalConfluenceError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except OSError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    summary = build_summary(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
