#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from lxml import etree

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

AC_NS = "http://atlassian.com/content"
RI_NS = "http://atlassian.com/resource/identifier"
NSMAP = {"ac": AC_NS, "ri": RI_NS}
DRAWIO_MACRO_NAMES = {"drawio", "inc-drawio"}


class ConfigError(Exception):
    pass


class FatalConfluenceError(Exception):
    pass


class PageProcessingError(Exception):
    pass


@dataclass(frozen=True)
class DiagramReference:
    diagram_name: str
    owner_page_id: str | None
    source: str


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


def normalize_site_url(raw: Any, field_name: str = "siteUrl") -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{field_name} must be a non-empty string")
    normalized = raw.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{field_name} must be a valid http(s) URL")
    if parsed.path not in {"", "/"}:
        raise ConfigError(f"{field_name} must be an Atlassian site root URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def confluence_base_url(site_url: str) -> str:
    return f"{normalize_site_url(site_url)}/wiki"


def parse_confluence_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def site_root(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def build_page_url(base_url: str, page: dict[str, Any]) -> str:
    links = page.get("_links") or {}
    webui = links.get("webui")
    if isinstance(webui, str) and webui:
        return urljoin(site_root(base_url), webui)
    return base_url


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


class ConfluenceClient:
    def __init__(self, base_url: str, email: str, token: str) -> None:
        self.base_url = base_url
        self.session = requests.Session()
        self.session.auth = (email, token)
        self.session.headers.update({"Accept": "application/json"})

    def _request(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        url = f"{self.base_url}{path}"
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Confluence page and attachment metadata into bundle.json.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to a JSON config file, or '-' to read from stdin.",
    )
    return parser.parse_args()


def load_metadata_config(config_arg: str) -> dict[str, Any]:
    raw = sys.stdin.read() if config_arg == "-" else Path(config_arg).read_text(encoding="utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON config: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")

    site_url = normalize_site_url(data.get("siteUrl"), field_name="siteUrl")
    titles = data.get("titles")
    output_dir = data.get("outputDir")
    space_key = data.get("spaceKey")

    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ConfigError("outputDir must be a non-empty string")
    if not isinstance(titles, list) or not titles:
        raise ConfigError("titles must be a non-empty array")
    if not all(isinstance(title, str) and title.strip() for title in titles):
        raise ConfigError("each title must be a non-empty string")
    if space_key is not None and (not isinstance(space_key, str) or not space_key.strip()):
        raise ConfigError("spaceKey must be a non-empty string when provided")

    return {
        "siteUrl": site_url,
        "titles": [title.strip() for title in titles],
        "outputDir": output_dir.strip(),
        "spaceKey": space_key.strip() if isinstance(space_key, str) else None,
    }


def normalize_attachment(raw: dict[str, Any], owner_page_id: str) -> dict[str, Any]:
    attachment_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    media_type = str(media_type_of_attachment(raw) or "").strip()
    download_path = str(((raw.get("_links") or {}).get("download")) or "").strip()

    if not attachment_id or not title:
        raise PageProcessingError(
            f"attachment metadata is missing id or title for owner page {owner_page_id}"
        )
    if not media_type:
        raise PageProcessingError(
            f"attachment metadata is missing mediaType for owner page {owner_page_id}"
        )
    if not download_path:
        raise PageProcessingError(
            f"attachment metadata is missing download link for owner page {owner_page_id}"
        )

    return {
        "id": attachment_id,
        "title": title,
        "ownerPageId": owner_page_id,
        "_links": {"download": download_path},
        "metadata": {"mediaType": media_type},
    }


def build_page_bundle(
    *,
    client: ConfluenceClient,
    extractor: DrawioReferenceExtractor,
    site_url: str,
    title: str,
    space_key: str | None,
) -> dict[str, Any]:
    pages = client.search_pages(title, space_key)
    selected = choose_page(title, pages)
    page_id = str(selected.get("id") or "").strip()
    if not page_id:
        raise PageProcessingError(f"missing page id for '{title}'")

    page = client.fetch_page(page_id)
    storage = (((page.get("body") or {}).get("storage") or {}).get("value")) or ""
    if not isinstance(storage, str) or not storage.strip():
        raise PageProcessingError(f"missing body.storage for '{title}'")

    version = int(((page.get("version") or {}).get("number")) or 0)
    source_url = build_page_url(site_url, page)
    references = extractor.extract(storage)

    owner_page_ids = [page_id]
    seen_owner_page_ids = {page_id}
    for reference in references:
        owner_page_id = (reference.owner_page_id or page_id).strip()
        if owner_page_id in seen_owner_page_ids:
            continue
        seen_owner_page_ids.add(owner_page_id)
        owner_page_ids.append(owner_page_id)

    attachments_by_page_id: dict[str, list[dict[str, Any]]] = {}
    for owner_page_id in owner_page_ids:
        attachments = client.list_attachments(owner_page_id)
        attachments_by_page_id[owner_page_id] = [
            normalize_attachment(attachment, owner_page_id)
            for attachment in attachments
            if isinstance(attachment, dict)
        ]

    return {
        "title": title,
        "pageId": page_id,
        "version": version,
        "sourceUrl": source_url,
        "storage": storage,
        "attachmentsByPageId": attachments_by_page_id,
    }


def write_bundle(bundle: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    try:
        args = parse_args()
        config = load_metadata_config(args.config)
        email, token = require_credentials()
    except (ConfigError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    client = ConfluenceClient(confluence_base_url(config["siteUrl"]), email, token)
    extractor = DrawioReferenceExtractor()

    try:
        bundle = {
            "siteUrl": config["siteUrl"],
            "pages": [
                build_page_bundle(
                    client=client,
                    extractor=extractor,
                    site_url=config["siteUrl"],
                    title=title,
                    space_key=config["spaceKey"],
                )
                for title in config["titles"]
            ],
        }
        write_bundle(bundle, Path.cwd() / "bundle.json")
    except (PageProcessingError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps({"bundleJson": str(Path.cwd() / "bundle.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
