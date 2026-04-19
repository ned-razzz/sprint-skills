from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parent / "scripts" / "run_mcp_export.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("run_mcp_export", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load run_mcp_export module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_module()


class RunMcpExportHybridTests(unittest.TestCase):
    def test_load_bundle_allows_partial_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_path = Path(tmp_dir) / "bundle.json"
            bundle_path.write_text('{"siteUrl": "https://example.atlassian.net"}', encoding="utf-8")
            payload = module.load_bundle(str(bundle_path))

        self.assertEqual(payload["siteUrl"], "https://example.atlassian.net")
        self.assertEqual(payload["pages"], [])

    def test_enrich_page_payload_backfills_storage_and_attachments_from_rest(self) -> None:
        storage = (
            '<ac:structured-macro ac:name="drawio" xmlns:ac="http://atlassian.com/content">'
            '<ac:parameter ac:name="diagramName">hwa</ac:parameter>'
            '<ac:parameter ac:name="pageId">32243721</ac:parameter>'
            "</ac:structured-macro>"
        )

        class FakeClient:
            def __init__(self) -> None:
                self.fetch_calls: list[str] = []
                self.attachment_calls: list[str] = []

            def fetch_page(self, page_id: str) -> dict[str, object]:
                self.fetch_calls.append(page_id)
                return {
                    "id": page_id,
                    "version": {"number": 7},
                    "body": {"storage": {"value": storage}},
                    "_links": {"webui": f"/spaces/ADP/pages/{page_id}/System+Architecture"},
                }

            def search_pages(self, title: str, space_key: str | None) -> list[dict[str, object]]:
                raise AssertionError("search_pages should not be called when pageId is already present")

            def list_attachments(self, page_id: str) -> list[dict[str, object]]:
                self.attachment_calls.append(page_id)
                return [
                    {
                        "id": "att-1",
                        "title": "hwa.drawio",
                        "metadata": {"mediaType": "application/vnd.jgraph.mxfile"},
                        "_links": {"download": "/download/attachments/32243721/hwa.drawio"},
                    }
                ]

        class Config:
            space_key = "ADP"

        client = FakeClient()
        page = module.enrich_page_payload(
            config=Config(),
            site_url="https://example.atlassian.net",
            title="System Architecture",
            page_payload={
                "title": "System Architecture",
                "pageId": "32243721",
            },
            get_rest_client=lambda: client,
        )

        self.assertEqual(client.fetch_calls, ["32243721"])
        self.assertEqual(client.attachment_calls, ["32243721"])
        self.assertEqual(page["pageId"], "32243721")
        self.assertEqual(page["version"], 7)
        self.assertEqual(
            page["sourceUrl"],
            "https://example.atlassian.net/spaces/ADP/pages/32243721/System+Architecture",
        )
        self.assertIn("diagramName", page["storage"])
        self.assertEqual(page["attachmentsByPageId"]["32243721"][0]["id"], "att-1")
        self.assertEqual(
            page["attachmentsByPageId"]["32243721"][0]["_links"]["download"],
            "/download/attachments/32243721/hwa.drawio",
        )


if __name__ == "__main__":
    unittest.main()
