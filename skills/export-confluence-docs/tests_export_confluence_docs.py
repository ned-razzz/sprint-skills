from __future__ import annotations

import importlib.util
import json
import os
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
render_doc = load_module("render_mermaid_doc", SCRIPTS_DIR / "render_mermaid_doc.py")


class ExportConfluenceDocsTests(unittest.TestCase):
    def test_load_config_resolves_relative_output_dir_from_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "baseUrl": "https://example.atlassian.net/wiki",
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

            self.assertEqual(config.base_url, "https://example.atlassian.net/wiki")
            self.assertEqual(config.titles, ["System Architecture"])
            self.assertEqual(config.space_key, "ADP")
            self.assertEqual(config.output_dir, tmp_path / "docs" / "exported")

    def test_normalized_xml_filename_prefers_diagram_slug(self) -> None:
        self.assertEqual(
            bundle.normalized_xml_filename("Hardware Architecture", "My Diagram.drawio"),
            "hardware-architecture.xml",
        )

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
            text = doc.read_text(encoding="utf-8")
            _, body = map_doc.split_front_matter(text)
            sections = map_doc.parse_sections(body)
            attrs = map_doc.placeholder_attrs(sections[0]["body"])
            self.assertEqual(attrs[0]["diagram_slug"], "hwa")

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
            front_matter, body = render_doc.split_front_matter(original)
            sections = render_doc.parse_sections(body)
            self.assertIn("confluence-drawio", sections[0]["body"])
            diagrams_by_xml = render_doc.load_diagrams(json.loads(diagram_json.read_text(encoding="utf-8")))
            pending = list(diagrams_by_xml.keys())
            xml_name = render_doc.section_xml_hint(sections[0]["body"], pending)
            self.assertEqual(xml_name, "hwa.xml")
            sections[0]["body"] = "```mermaid\nflowchart TB\n  a --> b\n```\n"
            rendered = front_matter
            for section in sections:
                rendered += section["heading_line"] + "\n" + render_doc.strip_drawio_placeholder(section["body"])
            self.assertIn("```mermaid", rendered)
            self.assertNotIn("confluence-drawio", rendered)


if __name__ == "__main__":
    unittest.main()
