#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from export_confluence_bundle import (
    ConfigError,
    ConfluenceClient,
    DiagramReference,
    DrawioReferenceExtractor,
    PageProcessingError,
    StorageToMarkdownConverter,
    build_attachment_download_url,
    build_page_url,
    choose_page,
    confluence_base_url,
    ensure_xml_content,
    find_fallback_drawio_attachments,
    find_matching_attachment,
    load_config,
    markdown_output_name,
    media_type_of_attachment,
    mermaid_slug,
    normalize_site_url,
    normalized_xml_filename,
    page_directory_name,
    page_frontmatter,
    require_credentials,
)
from map_doc_drawio import build_mapping
from render_drawio_mermaid import build_diagram_payload
from render_mermaid_doc import render_document

TEMP_ROOT = Path("/tmp/export-confluence-docs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the MCP-assisted Confluence export pipeline with REST metadata backfill and curl-based draw.io download.",
    )
    parser.add_argument("--config", required=True, help="Path to the current working directory config.json.")
    parser.add_argument(
        "--bundle-json",
        required=True,
        help="Path to JSON metadata assembled from Atlassian MCP, or - for stdin.",
    )
    parser.add_argument("--temp-root", default=str(TEMP_ROOT), help="Base temp directory for downloaded XML.")
    parser.add_argument("--curl-bin", default="curl", help="curl executable to use for XML downloads.")
    return parser.parse_args()


def load_bundle(bundle_arg: str) -> dict[str, Any]:
    raw = sys.stdin.read() if bundle_arg == "-" else Path(bundle_arg).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid MCP bundle JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError("MCP bundle must be a JSON object")
    payload["siteUrl"] = normalize_site_url(payload.get("siteUrl"), field_name="siteUrl")
    pages = payload.get("pages", [])
    if not isinstance(pages, list):
        raise ConfigError("MCP bundle pages must be an array when provided")
    payload["pages"] = pages
    return payload


def page_id_from_payload(page_payload: dict[str, Any]) -> str:
    return str(page_payload.get("pageId") or page_payload.get("id") or "").strip()


def page_title_from_payload(page_payload: dict[str, Any]) -> str:
    return str(page_payload.get("title") or "").strip()


def page_source_from_payload(page_payload: dict[str, Any]) -> str:
    return str(page_payload.get("sourceUrl") or page_payload.get("source") or "").strip()


def page_storage_from_payload(page_payload: dict[str, Any]) -> str:
    storage = page_payload.get("storage")
    if storage is None:
        storage = (((page_payload.get("body") or {}).get("storage") or {}).get("value")) or ""
    return storage if isinstance(storage, str) else ""


def page_version_from_payload(page_payload: dict[str, Any]) -> int:
    raw_version = page_payload.get("version")
    if isinstance(raw_version, dict):
        return int(raw_version.get("number") or 0)
    return int(raw_version or 0)


def attachment_download_path(raw: dict[str, Any]) -> str:
    download_path = str(raw.get("downloadPath") or "").strip()
    if download_path:
        return download_path
    links = raw.get("_links") or {}
    if isinstance(links, dict):
        return str(links.get("download") or "").strip()
    return ""


def attachment_has_required_metadata(raw: dict[str, Any]) -> bool:
    attachment_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    media_type = str(raw.get("mediaType") or media_type_of_attachment(raw) or "").strip()
    return bool(attachment_id and title and media_type and attachment_download_path(raw))


def extract_candidate_attachments(page_payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    attachments_by_page: dict[str, list[dict[str, Any]]] = {}

    raw_index = page_payload.get("attachmentsByPageId") or {}
    if isinstance(raw_index, dict):
        for owner_page_id, attachments in raw_index.items():
            if not isinstance(attachments, list):
                continue
            owner_key = str(owner_page_id).strip()
            if not owner_key:
                continue
            attachments_by_page[owner_key] = [item for item in attachments if isinstance(item, dict)]

    raw_attachments = page_payload.get("attachments") or []
    if isinstance(raw_attachments, list):
        for raw_attachment in raw_attachments:
            if not isinstance(raw_attachment, dict):
                continue
            owner_page_id = str(
                raw_attachment.get("ownerPageId")
                or raw_attachment.get("pageId")
                or page_id_from_payload(page_payload)
                or ""
            ).strip()
            if not owner_page_id:
                continue
            attachments_by_page.setdefault(owner_page_id, []).append(raw_attachment)

    return attachments_by_page


def index_bundle_pages(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for page_payload in bundle["pages"]:
        if not isinstance(page_payload, dict):
            raise ConfigError("each page entry must be an object")
        title = page_title_from_payload(page_payload)
        if not title:
            raise ConfigError("each page entry must include title")
        if title in indexed:
            raise ConfigError(f"duplicate page title in MCP bundle: {title}")
        indexed[title] = page_payload
    return indexed


def fetch_rest_page(
    *,
    page_payload: dict[str, Any],
    title: str,
    space_key: str | None,
    client: ConfluenceClient,
) -> dict[str, Any]:
    page_id = page_id_from_payload(page_payload)
    if page_id:
        return client.fetch_page(page_id)
    matches = client.search_pages(title, space_key)
    chosen = choose_page(title, matches)
    chosen_page_id = str(chosen.get("id") or "").strip()
    if not chosen_page_id:
        raise ConfigError(f"REST page search returned no page id for '{title}'")
    return client.fetch_page(chosen_page_id)


def complete_attachments_for_owner(
    attachments_by_page: dict[str, list[dict[str, Any]]],
    owner_page_id: str,
) -> bool:
    attachments = attachments_by_page.get(owner_page_id)
    if attachments is None:
        return False
    return all(attachment_has_required_metadata(item) for item in attachments)


def enrich_page_payload(
    *,
    config: Any,
    site_url: str,
    title: str,
    page_payload: dict[str, Any],
    get_rest_client: Callable[[], ConfluenceClient],
) -> dict[str, Any]:
    working = dict(page_payload)
    rest_page: dict[str, Any] | None = None

    if not page_id_from_payload(working) or not page_storage_from_payload(working) or not page_source_from_payload(working):
        rest_page = fetch_rest_page(
            page_payload=working,
            title=title,
            space_key=config.space_key,
            client=get_rest_client(),
        )
        working.setdefault("id", str(rest_page.get("id") or "").strip())
        working["pageId"] = page_id_from_payload(working) or str(rest_page.get("id") or "").strip()
        if not page_storage_from_payload(working):
            working["storage"] = ((((rest_page.get("body") or {}).get("storage") or {}).get("value")) or "")
        if not page_source_from_payload(working):
            working["sourceUrl"] = build_page_url(confluence_base_url(site_url), rest_page)
        if page_version_from_payload(working) == 0:
            working["version"] = rest_page.get("version") or 0

    page_id = page_id_from_payload(working)
    attachments_by_page = extract_candidate_attachments(working)
    references = DrawioReferenceExtractor().extract(page_storage_from_payload(working))
    owner_page_ids = {page_id}
    owner_page_ids.update(reference.owner_page_id or page_id for reference in references)

    needs_rest_attachments = any(
        not complete_attachments_for_owner(attachments_by_page, owner_page_id)
        for owner_page_id in owner_page_ids
    )
    if needs_rest_attachments:
        client = get_rest_client()
        for owner_page_id in owner_page_ids:
            if complete_attachments_for_owner(attachments_by_page, owner_page_id):
                continue
            attachments_by_page[owner_page_id] = client.list_attachments(owner_page_id)

    if attachments_by_page:
        working["attachmentsByPageId"] = attachments_by_page

    return normalize_page(working)


def normalize_attachment(raw: dict[str, Any], owner_page_id: str) -> dict[str, Any]:
    attachment_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not attachment_id or not title:
        raise ConfigError(f"attachment metadata is missing id or title for owner page {owner_page_id}")

    media_type = str(raw.get("mediaType") or media_type_of_attachment(raw) or "").strip()
    download_path = str(raw.get("downloadPath") or "").strip()
    if not download_path:
        links = raw.get("_links") or {}
        if isinstance(links, dict):
            download_path = str(links.get("download") or "").strip()

    normalized = dict(raw)
    normalized["id"] = attachment_id
    normalized["title"] = title
    normalized["ownerPageId"] = owner_page_id
    normalized["_links"] = {"download": download_path}
    normalized["metadata"] = {"mediaType": media_type}
    return normalized


def normalize_attachments_by_page(page_payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    attachments_by_page: dict[str, list[dict[str, Any]]] = {}

    raw_index = page_payload.get("attachmentsByPageId") or {}
    if raw_index:
        if not isinstance(raw_index, dict):
            raise ConfigError("attachmentsByPageId must be an object keyed by owner page id")
        for owner_page_id, attachments in raw_index.items():
            if not isinstance(attachments, list):
                raise ConfigError(f"attachmentsByPageId[{owner_page_id}] must be an array")
            owner_key = str(owner_page_id).strip()
            attachments_by_page[owner_key] = [
                normalize_attachment(item, owner_key) for item in attachments if isinstance(item, dict)
            ]
        return attachments_by_page

    raw_attachments = page_payload.get("attachments") or []
    if not isinstance(raw_attachments, list):
        raise ConfigError("attachments must be an array when provided")
    for raw_attachment in raw_attachments:
        if not isinstance(raw_attachment, dict):
            raise ConfigError("each attachment entry must be an object")
        owner_page_id = str(
            raw_attachment.get("ownerPageId")
            or raw_attachment.get("pageId")
            or page_payload.get("pageId")
            or page_payload.get("id")
            or ""
        ).strip()
        if not owner_page_id:
            raise ConfigError("attachment entry is missing ownerPageId")
        attachments_by_page.setdefault(owner_page_id, []).append(
            normalize_attachment(raw_attachment, owner_page_id)
        )
    return attachments_by_page


def normalize_page(page_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(page_payload, dict):
        raise ConfigError("each page entry must be an object")

    title = page_title_from_payload(page_payload)
    page_id = page_id_from_payload(page_payload)
    if not title or not page_id:
        raise ConfigError("each page entry must include title and pageId")

    version = page_version_from_payload(page_payload)
    source_url = page_source_from_payload(page_payload)
    storage = page_storage_from_payload(page_payload)
    if not isinstance(storage, str) or not storage.strip():
        raise ConfigError(f"page '{title}' is missing storage content")
    if not source_url:
        raise ConfigError(f"page '{title}' is missing sourceUrl")

    attachments_by_page = normalize_attachments_by_page(page_payload)
    return {
        "title": title,
        "pageId": page_id,
        "version": version,
        "sourceUrl": source_url,
        "storage": storage,
        "attachmentsByPageId": attachments_by_page,
    }


def normalize_pages(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for page_payload in bundle["pages"]:
        page = normalize_page(page_payload)
        if page["title"] in normalized:
            raise ConfigError(f"duplicate page title in MCP bundle: {page['title']}")
        normalized[page["title"]] = page
    return normalized


def require_download_credentials() -> tuple[str, str]:
    email = os.environ.get("CONFLUENCE_EMAIL", "").strip()
    token = os.environ.get("CONFLUENCE_API_TOKEN", "").strip()
    if not email or not token:
        raise ConfigError("CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN must be set before curl download")
    return email, token


def curl_download_xml(curl_bin: str, url: str, output_path: Path) -> dict[str, Any]:
    email, token = require_download_credentials()
    command = [
        curl_bin,
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--user",
        f"{email}:{token}",
        "--output",
        str(output_path),
        url,
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise PageProcessingError(f"curl executable not found: {curl_bin}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        detail = f": {stderr}" if stderr else ""
        raise PageProcessingError(f"curl download failed for {url}{detail}") from exc

    content = output_path.read_bytes()
    return {
        "command": [
            curl_bin,
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--user",
            "<redacted>",
            "--output",
            str(output_path),
            url,
        ],
        "size": len(content),
    }


def export_drawio_xml_with_curl(
    *,
    site_url: str,
    references: list[DiagramReference],
    page_id: str,
    attachments_by_page: dict[str, list[dict[str, Any]]],
    temp_xml_dir: Path,
    curl_bin: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    saved: list[dict[str, Any]] = []
    warnings: list[str] = []
    processed_attachment_ids: set[str] = set()

    if references:
        reference_items = references
    else:
        reference_items = [
            DiagramReference(
                diagram_name=str(attachment.get("title") or "diagram"),
                owner_page_id=page_id,
                source="attachment-fallback",
            )
            for attachment in find_fallback_drawio_attachments(attachments_by_page.get(page_id, []))
        ]

    for reference in reference_items:
        owner_page_id = reference.owner_page_id or page_id
        attachments = attachments_by_page.get(owner_page_id, [])
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

        download_url = build_attachment_download_url(site_url, attachment)
        if not download_url:
            warnings.append(
                f"attachment download link missing for '{reference.diagram_name}' "
                f"(ownerPageId={owner_page_id})"
            )
            continue

        filename = normalized_xml_filename(
            diagram_name=reference.diagram_name,
            attachment_title=str(attachment.get("title") or reference.diagram_name),
        )
        output_path = temp_xml_dir / filename
        curl_result = curl_download_xml(curl_bin, download_url, output_path)
        ensure_xml_content(output_path.read_bytes(), str(attachment.get("title") or reference.diagram_name))
        processed_attachment_ids.add(attachment_id)
        saved.append(
            {
                "diagramName": reference.diagram_name,
                "diagramSlug": mermaid_slug(reference.diagram_name),
                "attachmentTitle": str(attachment.get("title") or ""),
                "attachmentId": attachment_id,
                "ownerPageId": owner_page_id,
                "downloadUrl": download_url,
                "path": str(output_path),
                "xml": output_path.name,
                "source": reference.source,
                "download": curl_result,
            }
        )

    if not saved and not references:
        warnings.append("no draw.io references or matching draw.io attachments found")
    return saved, warnings


def process_page(
    *,
    config: Any,
    site_url: str,
    page: dict[str, Any],
    temp_root: Path,
    curl_bin: str,
) -> dict[str, Any]:
    references = DrawioReferenceExtractor().extract(page["storage"])
    temp_xml_dir = temp_root / page_directory_name(page["title"], page["pageId"])
    temp_xml_dir.mkdir(parents=True, exist_ok=True)
    for xml_file in temp_xml_dir.glob("*.xml"):
        xml_file.unlink()

    markdown = StorageToMarkdownConverter(references).convert(page["storage"])
    output_path = config.output_dir / f"{markdown_output_name(page['title'], page['pageId'])}.md"
    content = page_frontmatter(
        page["title"],
        page["pageId"],
        page["version"],
        page["sourceUrl"],
        markdown,
    )
    output_path.write_text(content, encoding="utf-8")

    xml_entries, warnings = export_drawio_xml_with_curl(
        site_url=site_url,
        references=references,
        page_id=page["pageId"],
        attachments_by_page=page["attachmentsByPageId"],
        temp_xml_dir=temp_xml_dir,
        curl_bin=curl_bin,
    )

    mapping = build_mapping(output_path, str(temp_xml_dir)) if xml_entries else {"sections": [], "xml_files": []}
    diagram_payload = build_diagram_payload(mapping, str(temp_xml_dir)) if xml_entries else {"diagrams": []}

    if diagram_payload["diagrams"]:
        rendered = render_document(
            output_path,
            {item["xml"]: item for item in diagram_payload["diagrams"]},
        )
        output_path.write_text(rendered, encoding="utf-8")

    if warnings and xml_entries:
        status = "partial"
    elif warnings and references:
        status = "failed"
    else:
        status = "succeeded"

    return {
        "title": page["title"],
        "status": status,
        "pageId": page["pageId"],
        "version": page["version"],
        "source": page["sourceUrl"],
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
        bundle = load_bundle(args.bundle_json)
        site_url = bundle["siteUrl"]
        pages_by_title = index_bundle_pages(bundle)
    except (ConfigError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    config.output_dir.mkdir(parents=True, exist_ok=True)
    temp_root = Path(args.temp_root).expanduser()
    temp_root.mkdir(parents=True, exist_ok=True)
    rest_client: ConfluenceClient | None = None

    def get_rest_client() -> ConfluenceClient:
        nonlocal rest_client
        if rest_client is None:
            email, token = require_credentials()
            rest_client = ConfluenceClient(confluence_base_url(site_url), email, token)
        return rest_client

    results: list[dict[str, Any]] = []
    for title in config.titles:
        try:
            page = enrich_page_payload(
                config=config,
                site_url=site_url,
                title=title,
                page_payload=pages_by_title.get(title, {"title": title}),
                get_rest_client=get_rest_client,
            )
            results.append(
                process_page(
                    config=config,
                    site_url=site_url,
                    page=page,
                    temp_root=temp_root,
                    curl_bin=args.curl_bin,
                )
            )
        except (ConfigError, OSError, PageProcessingError, ValueError) as exc:
            results.append({"title": title, "status": "failed", "reason": str(exc)})

    summary = build_summary(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
