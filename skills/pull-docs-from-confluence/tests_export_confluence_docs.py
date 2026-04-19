from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SKILL_DIR / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bundle = load_module("export_confluence_bundle", SCRIPTS_DIR / "export_confluence_bundle.py")
map_doc = load_module("map_doc_drawio", SCRIPTS_DIR / "map_doc_drawio.py")
extract_ir = load_module("extract_drawio_ir", SCRIPTS_DIR / "extract_drawio_ir.py")
render_mermaid = load_module("render_drawio_mermaid", SCRIPTS_DIR / "render_drawio_mermaid.py")
render_doc = load_module("render_mermaid_doc", SCRIPTS_DIR / "render_mermaid_doc.py")
run_mcp = load_module("run_mcp_export", SCRIPTS_DIR / "run_mcp_export.py")


class ExportConfluenceDocsTests(unittest.TestCase):
    def write_xml(self, path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")

    def test_load_config_resolves_relative_output_dir_from_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "titles": ["System Architecture"],
                        "spaceKey": "ADP",
                        "outputDir": "./docs/exported",
                    }
                ),
                encoding="utf-8",
            )
            previous_cwd = Path.cwd()
            try:
                os.chdir(tmp_path)
                config = bundle.load_config(str(config_path))
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(config.titles, ["System Architecture"])
            self.assertEqual(config.space_key, "ADP")
            self.assertEqual(config.output_dir, tmp_path / "docs" / "exported")

    def test_normalize_site_url_rejects_wiki_path(self) -> None:
        with self.assertRaisesRegex(bundle.ConfigError, "siteUrl must be an Atlassian site root URL"):
            bundle.normalize_site_url("https://example.atlassian.net/wiki")

    def test_confluence_base_url_appends_wiki(self) -> None:
        self.assertEqual(
            bundle.confluence_base_url("https://example.atlassian.net"),
            "https://example.atlassian.net/wiki",
        )

    def test_normalized_xml_filename_prefers_diagram_slug(self) -> None:
        self.assertEqual(
            bundle.normalized_xml_filename("Hardware Architecture", "My Diagram.drawio"),
            "hardware-architecture.xml",
        )

    def test_build_attachment_download_url_uses_site_root(self) -> None:
        attachment = {"_links": {"download": "/download/attachments/123/hwa.drawio?api=v2"}}
        self.assertEqual(
            bundle.build_attachment_download_url(
                "https://example.atlassian.net",
                attachment,
            ),
            "https://example.atlassian.net/download/attachments/123/hwa.drawio?api=v2",
        )

    def test_build_attachment_download_url_returns_empty_string_without_link(self) -> None:
        self.assertEqual(
            bundle.build_attachment_download_url(
                "https://example.atlassian.net",
                {"id": "123"},
            ),
            "",
        )

    def test_export_confluence_bundle_requires_site_url_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config_path.write_text(
                json.dumps({"titles": ["System Architecture"], "outputDir": "./docs"}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPTS_DIR / "export_confluence_bundle.py"),
                    "--config",
                    str(config_path),
                ],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--site-url", result.stderr)

    def test_require_credentials_fails_without_env_vars(self) -> None:
        previous_email = os.environ.pop("CONFLUENCE_EMAIL", None)
        previous_token = os.environ.pop("CONFLUENCE_API_TOKEN", None)
        try:
            with self.assertRaises(bundle.ConfigError):
                bundle.require_credentials()
        finally:
            if previous_email is not None:
                os.environ["CONFLUENCE_EMAIL"] = previous_email
            if previous_token is not None:
                os.environ["CONFLUENCE_API_TOKEN"] = previous_token

    def test_drawio_macro_renders_placeholder(self) -> None:
        converter = bundle.StorageToMarkdownConverter([])
        storage = textwrap.dedent(
            """
            <h1>Architecture</h1>
            <ac:structured-macro ac:name="drawio">
              <ac:parameter ac:name="diagramName">HWA</ac:parameter>
              <ac:parameter ac:name="pageId">32243721</ac:parameter>
            </ac:structured-macro>
            """
        ).strip()
        rendered = converter.convert(storage)
        self.assertIn('confluence-drawio diagram="HWA"', rendered)
        self.assertIn('diagram_slug="hwa"', rendered)

    def test_drawio_adf_extension_renders_placeholder_with_hyphen_keys(self) -> None:
        converter = bundle.StorageToMarkdownConverter([])
        storage = textwrap.dedent(
            """
            <h1>Architecture</h1>
            <ac:adf-extension>
              <ac:adf-node type="extension">
                <ac:adf-attribute key="extension-key">app/static/drawio</ac:adf-attribute>
                <ac:adf-attribute key="parameters">
                  <ac:adf-parameter key="guest-params">
                    <ac:adf-parameter key="diagram-name">HWA</ac:adf-parameter>
                    <ac:adf-parameter key="page-id">32243721</ac:adf-parameter>
                  </ac:adf-parameter>
                </ac:adf-attribute>
              </ac:adf-node>
            </ac:adf-extension>
            """
        ).strip()
        rendered = converter.convert(storage)
        self.assertIn('confluence-drawio diagram="HWA"', rendered)
        self.assertIn('owner_page_id="32243721"', rendered)

    def test_extract_adf_extension_references_supports_hyphen_keys(self) -> None:
        extractor = bundle.DrawioReferenceExtractor()
        storage = textwrap.dedent(
            """
            <ac:adf-extension>
              <ac:adf-node type="extension">
                <ac:adf-attribute key="extension-key">app/static/inc-drawio</ac:adf-attribute>
                <ac:adf-attribute key="parameters">
                  <ac:adf-parameter key="guest-params">
                    <ac:adf-parameter key="diagram-name">state_diagram.drawio</ac:adf-parameter>
                    <ac:adf-parameter key="page-id">52166707</ac:adf-parameter>
                  </ac:adf-parameter>
                </ac:adf-attribute>
              </ac:adf-node>
            </ac:adf-extension>
            """
        ).strip()
        references = extractor.extract(storage)
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].diagram_name, "state_diagram.drawio")
        self.assertEqual(references[0].owner_page_id, "52166707")

    def test_export_drawio_xml_includes_download_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            temp_xml_dir = tmp_path / "system-architecture--32243721"
            temp_xml_dir.mkdir()

            class StubClient:
                base_url = "https://example.atlassian.net/wiki"

                def list_attachments(self, page_id: str):
                    return [
                        {
                            "id": "att-1",
                            "title": "HWA.drawio",
                            "metadata": {"mediaType": "application/vnd.jgraph.mxfile"},
                            "_links": {"download": "/download/attachments/32243721/HWA.drawio"},
                        }
                    ]

                def download_attachment(self, download_path: str):
                    self.last_download_path = download_path
                    return b"<mxGraphModel><root/></mxGraphModel>"

            client = StubClient()
            saved, warnings = bundle.export_drawio_xml(
                client=client,
                references=[
                    bundle.DiagramReference(
                        diagram_name="HWA",
                        owner_page_id="32243721",
                        source="structured-macro:drawio",
                    )
                ],
                page_id="32243721",
                temp_xml_dir=temp_xml_dir,
                attachment_cache={},
            )

            self.assertEqual(warnings, [])
            self.assertEqual(saved[0]["downloadUrl"], "https://example.atlassian.net/download/attachments/32243721/HWA.drawio")
            self.assertEqual(client.last_download_path, "/download/attachments/32243721/HWA.drawio")

    def test_export_drawio_xml_warns_when_download_link_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            temp_xml_dir = tmp_path / "system-architecture--32243721"
            temp_xml_dir.mkdir()

            class StubClient:
                base_url = "https://example.atlassian.net/wiki"

                def list_attachments(self, page_id: str):
                    return [
                        {
                            "id": "att-1",
                            "title": "HWA.drawio",
                            "metadata": {"mediaType": "application/vnd.jgraph.mxfile"},
                            "_links": {},
                        }
                    ]

            saved, warnings = bundle.export_drawio_xml(
                client=StubClient(),
                references=[
                    bundle.DiagramReference(
                        diagram_name="HWA",
                        owner_page_id="32243721",
                        source="structured-macro:drawio",
                    )
                ],
                page_id="32243721",
                temp_xml_dir=temp_xml_dir,
                attachment_cache={},
            )

            self.assertEqual(saved, [])
            self.assertEqual(
                warnings,
                ["attachment download link missing for 'HWA' (ownerPageId=32243721)"],
            )

    def test_map_doc_uses_placeholder_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "system-architecture.md"
            xml_dir = tmp_path / "system-architecture--32243721"
            xml_dir.mkdir()
            (xml_dir / "hwa.xml").write_text("<mxGraphModel><root/></mxGraphModel>", encoding="utf-8")
            doc.write_text(
                textwrap.dedent(
                    """\
                    ---
                    confluence_page_id: "32243721"
                    ---

                    # Hardware
                    <!-- confluence-drawio diagram="HWA" diagram_slug="hwa" owner_page_id="32243721" source="structured-macro:drawio" -->
                    """
                ),
                encoding="utf-8",
            )
            resolved = map_doc.resolve_xml_dir(doc, "32243721", str(xml_dir))
            self.assertEqual(resolved, xml_dir)
            payload = map_doc.build_mapping(doc, str(xml_dir))
            self.assertEqual(payload["sections"][0]["diagram_slug"], "hwa")
            self.assertEqual(payload["sections"][0]["xml"], "hwa.xml")

    def test_render_mermaid_doc_removes_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "system-architecture.md"
            doc.write_text(
                textwrap.dedent(
                    """\
                    ---
                    confluence_page_id: "32243721"
                    ---

                    # Hardware
                    <!-- confluence-drawio diagram="HWA" diagram_slug="hwa" owner_page_id="32243721" source="structured-macro:drawio" -->
                    """
                ),
                encoding="utf-8",
            )
            diagram_json = tmp_path / "diagram.json"
            diagram_json.write_text(
                json.dumps(
                    {"diagrams": [{"xml": "hwa.xml", "mermaid": "flowchart TB\n  a --> b"}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original = doc.read_text(encoding="utf-8")
            diagrams_by_xml = render_doc.load_diagrams(json.loads(diagram_json.read_text(encoding="utf-8")))
            rendered = render_doc.render_document(doc, diagrams_by_xml)
            self.assertIn("```mermaid", rendered)
            self.assertIn('confluence-drawio-rendered diagram="HWA"', rendered)
            self.assertNotIn('confluence-drawio diagram="HWA"', rendered)
            self.assertNotEqual(rendered, original)

    def test_render_mermaid_doc_replaces_each_placeholder_by_matching_drawio_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "system-architecture.md"
            doc.write_text(
                textwrap.dedent(
                    """\
                    ---
                    confluence_page_id: "32243721"
                    ---

                    # Hardware
                    <!-- confluence-drawio diagram="HWA.drawio" diagram_slug="hwa" owner_page_id="32243721" source="structured-macro:drawio" -->

                    # Software
                    <!-- confluence-drawio diagram="SAS.drawio" diagram_slug="sas" owner_page_id="32243721" source="structured-macro:drawio" -->
                    """
                ),
                encoding="utf-8",
            )
            diagram_json = tmp_path / "diagram.json"
            diagram_json.write_text(
                json.dumps(
                    {
                        "diagrams": [
                            {"xml": "sas.xml", "mermaid": "flowchart TB\n  soft_a --> soft_b"},
                            {"xml": "hwa.xml", "mermaid": "flowchart TB\n  hard_a --> hard_b"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            subprocess.run(
                [
                    "python3",
                    str(SCRIPTS_DIR / "render_mermaid_doc.py"),
                    "--doc",
                    str(doc),
                    "--diagram-json",
                    str(diagram_json),
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            rendered = doc.read_text(encoding="utf-8")
            self.assertIn('# Hardware\n<!-- confluence-drawio-rendered diagram="HWA.drawio"', rendered)
            self.assertIn("flowchart TB\n  hard_a --> hard_b", rendered)
            self.assertIn('# Software\n<!-- confluence-drawio-rendered diagram="SAS.drawio"', rendered)
            self.assertIn("flowchart TB\n  soft_a --> soft_b", rendered)
            self.assertNotIn('confluence-drawio diagram="', rendered)

    def test_render_mermaid_doc_rerenders_existing_marker_block_without_touching_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "system-architecture.md"
            doc.write_text(
                textwrap.dedent(
                    """\
                    ---
                    confluence_page_id: "32243721"
                    ---

                    Intro paragraph.

                    # Hardware
                    Before diagram.
                    <!-- confluence-drawio-rendered diagram="HWA" diagram_slug="hwa" owner_page_id="32243721" source="structured-macro:drawio" xml="hwa.xml" -->
                    ```mermaid
                    flowchart TB
                      old --> graph
                    ```
                    After diagram.
                    """
                ),
                encoding="utf-8",
            )
            rendered = render_doc.render_document(
                doc,
                {"hwa.xml": {"xml": "hwa.xml", "mermaid": "flowchart TB\n  fresh --> graph"}},
            )
            self.assertIn("Intro paragraph.", rendered)
            self.assertIn("Before diagram.", rendered)
            self.assertIn("After diagram.", rendered)
            self.assertIn('xml="hwa.xml"', rendered)
            self.assertIn("flowchart TB\n  fresh --> graph", rendered)
            self.assertNotIn("old --> graph", rendered)

    def test_extract_drawio_ir_reports_connected_unlabeled_vertices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            xml_path = tmp_path / "bad.xml"
            self.write_xml(
                xml_path,
                """
                <mxGraphModel>
                  <root>
                    <mxCell id="0" />
                    <mxCell id="1" parent="0" />
                    <mxCell id="n1" value="Gateway" style="rounded=1;" vertex="1" parent="1">
                      <mxGeometry x="20" y="20" width="120" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="n2" style="rounded=1;" vertex="1" parent="1">
                      <mxGeometry x="220" y="20" width="120" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="e1" edge="1" source="n1" target="n2" parent="1">
                      <mxGeometry relative="1" as="geometry" />
                    </mxCell>
                  </root>
                </mxGraphModel>
                """,
            )

            payload = extract_ir.extract_ir(xml_path)

            self.assertEqual(payload["issues"]["connected_unlabeled_vertices"][0]["id"], "n2")
            self.assertEqual(payload["issues"]["unsupported_edges"][0]["reason"], "edge references an unlabeled vertex")

    def test_render_drawio_mermaid_renders_architecture_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            xml_dir = tmp_path / "xml"
            xml_dir.mkdir()
            xml_path = xml_dir / "hwa.xml"
            self.write_xml(
                xml_path,
                """
                <mxGraphModel>
                  <root>
                    <mxCell id="0" />
                    <mxCell id="1" parent="0" />
                    <mxCell id="c1" value="Edge Cluster" style="rounded=1;" vertex="1" parent="1">
                      <mxGeometry x="20" y="20" width="360" height="360" as="geometry" />
                    </mxCell>
                    <mxCell id="n1" value="Camera" style="rounded=1;" vertex="1" parent="1">
                      <mxGeometry x="60" y="80" width="120" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="n2" value="Camera" style="rounded=1;" vertex="1" parent="1">
                      <mxGeometry x="220" y="80" width="120" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="n3" value="API Service" style="rounded=1;" vertex="1" parent="1">
                      <mxGeometry x="140" y="180" width="140" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="n4" value="Message DB" style="shape=cylinder3;" vertex="1" parent="1">
                      <mxGeometry x="140" y="280" width="140" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="n5" value="Operator Console" style="ellipse;" vertex="1" parent="1">
                      <mxGeometry x="440" y="180" width="160" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="t1" value="UI" style="text;" vertex="1" parent="1">
                      <mxGeometry x="20" y="0" width="60" height="20" as="geometry" />
                    </mxCell>
                    <mxCell id="e1" value="RTSP" edge="1" source="n1" target="n3" parent="1">
                      <mxGeometry relative="1" as="geometry" />
                    </mxCell>
                    <mxCell id="e2" edge="1" source="n2" target="n3" parent="1">
                      <mxGeometry relative="1" as="geometry" />
                    </mxCell>
                    <mxCell id="e3" value="TCP" edge="1" source="n3" target="n4" parent="1">
                      <mxGeometry relative="1" as="geometry" />
                    </mxCell>
                    <mxCell id="e4" value="HTTPS" edge="1" source="n5" target="n3" parent="1">
                      <mxGeometry relative="1" as="geometry" />
                    </mxCell>
                  </root>
                </mxGraphModel>
                """,
            )
            mapping = {
                "document": {"xml_dir": str(xml_dir)},
                "sections": [{"heading": "Hardware", "xml": "hwa.xml", "mode": "placeholder"}],
                "xml_files": ["hwa.xml"],
            }

            payload = render_mermaid.build_diagram_payload(mapping)

            self.assertEqual(
                payload["diagrams"][0]["mermaid"],
                textwrap.dedent(
                    """\
                    flowchart TB
                      subgraph sg_edge_cluster["Edge Cluster"]
                        camera["Camera"]
                        camera_2["Camera"]
                        api_service["API Service"]
                        message_db[("Message DB")]
                      end
                      operator_console(("Operator Console"))
                      api_service -->|TCP| message_db
                      camera -->|RTSP| api_service
                      camera_2 --> api_service
                      operator_console -->|HTTPS| api_service"""
                ),
            )
            self.assertNotIn("UI", payload["diagrams"][0]["mermaid"])

    def test_render_drawio_mermaid_end_to_end_replaces_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "system-architecture.md"
            xml_dir = tmp_path / "system-architecture--32243721"
            xml_dir.mkdir()
            self.write_xml(
                xml_dir / "hwa.xml",
                """
                <mxGraphModel>
                  <root>
                    <mxCell id="0" />
                    <mxCell id="1" parent="0" />
                    <mxCell id="n1" value="Gateway" style="rounded=1;" vertex="1" parent="1">
                      <mxGeometry x="20" y="20" width="120" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="n2" value="Robot Service" style="rounded=1;" vertex="1" parent="1">
                      <mxGeometry x="220" y="20" width="140" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="e1" value="ROS" edge="1" source="n1" target="n2" parent="1">
                      <mxGeometry relative="1" as="geometry" />
                    </mxCell>
                  </root>
                </mxGraphModel>
                """,
            )
            doc.write_text(
                textwrap.dedent(
                    """\
                    ---
                    confluence_page_id: "32243721"
                    ---

                    # Hardware
                    <!-- confluence-drawio diagram="HWA" diagram_slug="hwa" owner_page_id="32243721" source="structured-macro:drawio" -->
                    """
                ),
                encoding="utf-8",
            )

            mapping_result = subprocess.run(
                [
                    "python3",
                    str(SCRIPTS_DIR / "map_doc_drawio.py"),
                    "--doc",
                    str(doc),
                    "--xml-dir",
                    str(xml_dir),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            map_payload = json.loads(mapping_result.stdout)
            diagram_payload = render_mermaid.build_diagram_payload(map_payload)
            diagram_json = tmp_path / "diagram.json"
            diagram_json.write_text(json.dumps(diagram_payload, ensure_ascii=False), encoding="utf-8")

            subprocess.run(
                [
                    "python3",
                    str(SCRIPTS_DIR / "render_mermaid_doc.py"),
                    "--doc",
                    str(doc),
                    "--diagram-json",
                    str(diagram_json),
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            rendered = doc.read_text(encoding="utf-8")
            self.assertIn("```mermaid", rendered)
            self.assertIn('gateway["Gateway"]', rendered)
            self.assertIn("gateway -->|ROS| robot_service", rendered)
            self.assertIn("confluence-drawio-rendered", rendered)
            self.assertNotIn('confluence-drawio diagram="', rendered)

    def test_map_doc_fails_when_xml_is_unmapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "system-architecture.md"
            xml_dir = tmp_path / "system-architecture--32243721"
            xml_dir.mkdir()
            (xml_dir / "hwa.xml").write_text("<mxGraphModel><root/></mxGraphModel>", encoding="utf-8")
            (xml_dir / "sas.xml").write_text("<mxGraphModel><root/></mxGraphModel>", encoding="utf-8")
            doc.write_text(
                textwrap.dedent(
                    """\
                    ---
                    confluence_page_id: "32243721"
                    ---

                    # Hardware
                    <!-- confluence-drawio diagram="HWA" diagram_slug="hwa" owner_page_id="32243721" source="structured-macro:drawio" -->
                    """
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unmapped xml files: sas.xml"):
                map_doc.build_mapping(doc, str(xml_dir))

    def test_run_mcp_export_uses_curl_and_renders_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            output_dir = tmp_path / "docs"
            config_path.write_text(
                json.dumps(
                    {
                        "titles": ["System Architecture"],
                        "outputDir": str(output_dir),
                    }
                ),
                encoding="utf-8",
            )
            bundle_path = tmp_path / "bundle.json"
            bundle_path.write_text(
                json.dumps(
                    {
                        "siteUrl": "https://example.atlassian.net",
                        "pages": [
                            {
                                "title": "System Architecture",
                                "pageId": "32243721",
                                "version": 7,
                                "sourceUrl": "https://example.atlassian.net/wiki/spaces/ADP/pages/32243721/System+Architecture",
                                "storage": (
                                    "<h1>Hardware</h1>"
                                    '<ac:structured-macro ac:name="drawio">'
                                    '<ac:parameter ac:name="diagramName">HWA</ac:parameter>'
                                    '<ac:parameter ac:name="pageId">32243721</ac:parameter>'
                                    "</ac:structured-macro>"
                                ),
                                "attachmentsByPageId": {
                                    "32243721": [
                                        {
                                            "id": "att-1",
                                            "title": "HWA.drawio",
                                            "mediaType": "application/vnd.jgraph.mxfile",
                                            "downloadPath": "/download/attachments/32243721/HWA.drawio",
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            curl_path = tmp_path / "fake-curl.sh"
            curl_path.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -eu
                    out=""
                    while [ "$#" -gt 0 ]; do
                      case "$1" in
                        --output)
                          out="$2"
                          shift 2
                          ;;
                        *)
                          shift
                          ;;
                      esac
                    done
                    cat > "$out" <<'EOF'
                    <mxGraphModel><root><mxCell id="0" /><mxCell id="1" parent="0" /><mxCell id="n1" value="Gateway" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="20" y="20" width="120" height="60" as="geometry" /></mxCell><mxCell id="n2" value="Robot Service" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="220" y="20" width="140" height="60" as="geometry" /></mxCell><mxCell id="e1" value="ROS" edge="1" source="n1" target="n2" parent="1"><mxGeometry relative="1" as="geometry" /></mxCell></root></mxGraphModel>
                    EOF
                    """
                ),
                encoding="utf-8",
            )
            curl_path.chmod(0o755)

            env = os.environ.copy()
            env["CONFLUENCE_EMAIL"] = "user@example.com"
            env["CONFLUENCE_API_TOKEN"] = "secret-token"

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPTS_DIR / "run_mcp_export.py"),
                    "--config",
                    str(config_path),
                    "--bundle-json",
                    str(bundle_path),
                    "--temp-root",
                    str(tmp_path / "tmp-export"),
                    "--curl-bin",
                    str(curl_path),
                ],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )

            payload = json.loads(result.stdout)
            self.assertEqual(payload["succeeded"], 1)
            markdown_path = output_dir / "system-architecture.md"
            rendered = markdown_path.read_text(encoding="utf-8")
            self.assertIn("confluence-drawio-rendered", rendered)
            self.assertIn('gateway["Gateway"]', rendered)
            self.assertIn("gateway -->|ROS| robot_service", rendered)
            download = payload["results"][0]["xmlEntries"][0]["download"]
            self.assertEqual(download["command"][0], str(curl_path))
            self.assertEqual(download["command"][5], "--user")
            self.assertEqual(download["command"][6], "<redacted>")

    def test_run_mcp_export_requires_bundle_site_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config_path.write_text(
                json.dumps({"titles": ["System Architecture"], "outputDir": str(tmp_path / "docs")}),
                encoding="utf-8",
            )
            bundle_path = tmp_path / "bundle.json"
            bundle_path.write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "title": "System Architecture",
                                "pageId": "32243721",
                                "version": 7,
                                "sourceUrl": "https://example.atlassian.net/wiki/spaces/ADP/pages/32243721/System+Architecture",
                                "storage": "<h1>Hardware</h1>",
                                "attachmentsByPageId": {"32243721": []},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPTS_DIR / "run_mcp_export.py"),
                    "--config",
                    str(config_path),
                    "--bundle-json",
                    str(bundle_path),
                    "--temp-root",
                    str(tmp_path / "tmp-export"),
                ],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("siteUrl must be a non-empty string", result.stderr)

    def test_run_mcp_export_skips_credentials_for_pages_without_diagrams(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            output_dir = tmp_path / "docs"
            config_path.write_text(
                json.dumps(
                    {
                        "titles": ["Notes"],
                        "outputDir": str(output_dir),
                    }
                ),
                encoding="utf-8",
            )
            bundle_path = tmp_path / "bundle.json"
            bundle_path.write_text(
                json.dumps(
                    {
                        "siteUrl": "https://example.atlassian.net",
                        "pages": [
                            {
                                "title": "Notes",
                                "pageId": "11",
                                "version": 1,
                                "sourceUrl": "https://example.atlassian.net/wiki/spaces/ADP/pages/11/Notes",
                                "storage": "<h1>Notes</h1><p>No diagrams here.</p>",
                                "attachmentsByPageId": {"11": []},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.pop("CONFLUENCE_EMAIL", None)
            env.pop("CONFLUENCE_API_TOKEN", None)

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPTS_DIR / "run_mcp_export.py"),
                    "--config",
                    str(config_path),
                    "--bundle-json",
                    str(bundle_path),
                    "--temp-root",
                    str(tmp_path / "tmp-export"),
                ],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )

            payload = json.loads(result.stdout)
            self.assertEqual(payload["succeeded"], 1)
            self.assertEqual(payload["results"][0]["diagramCount"], 0)

    def test_render_drawio_mermaid_fails_on_unsupported_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            xml_dir = tmp_path / "xml"
            xml_dir.mkdir()
            self.write_xml(
                xml_dir / "bad.xml",
                """
                <mxGraphModel>
                  <root>
                    <mxCell id="0" />
                    <mxCell id="1" parent="0" />
                    <mxCell id="n1" value="Gateway" style="rounded=1;" vertex="1" parent="1">
                      <mxGeometry x="20" y="20" width="120" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="n2" style="rounded=1;" vertex="1" parent="1">
                      <mxGeometry x="220" y="20" width="120" height="60" as="geometry" />
                    </mxCell>
                    <mxCell id="e1" edge="1" source="n1" target="n2" parent="1">
                      <mxGeometry relative="1" as="geometry" />
                    </mxCell>
                  </root>
                </mxGraphModel>
                """,
            )
            map_json = tmp_path / "map.json"
            map_json.write_text(
                json.dumps(
                    {
                        "document": {"xml_dir": str(xml_dir)},
                        "sections": [{"heading": "Hardware", "xml": "bad.xml", "mode": "placeholder"}],
                        "xml_files": ["bad.xml"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPTS_DIR / "render_drawio_mermaid.py"),
                    "--map-json",
                    str(map_json),
                ],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported edges", result.stderr)


if __name__ == "__main__":
    unittest.main()
