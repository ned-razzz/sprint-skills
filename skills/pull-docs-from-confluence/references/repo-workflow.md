# Repository Workflow

Use this skill with repositories that provide a current working directory `config.json` describing the Confluence titles to export and the final Markdown output directory.

Assume the page body and attachment metadata were retrieved through Atlassian MCP and passed to `scripts/run_mcp_export.py` as bundle JSON. The runner downloads the draw.io bytes locally with `curl` before these repository layout rules are applied.

## Export Layout

- Read `./config.json` from the current working directory before deciding export targets.
- When `config.json` is present and valid, export only the page titles listed in `titles`.
- Final Markdown documents live at `<outputDir>/*.md`, where `outputDir` comes from `config.json`.
- Each exported document includes `confluence_page_id` in YAML front matter.
- Temporary XML lives at `/tmp/export-confluence-docs/<markdown-stem>--<confluence_page_id>/`.
- XML files come from draw.io attachments discovered through Atlassian MCP metadata and downloaded only with `curl` plus Confluence credentials.
- XML files are normalized to `<diagram-slug>.xml` when the Confluence macro exposes `diagramName`.
- If `config.json` is missing or invalid, surface that condition and stop.

## Placeholder Contract

- Raw Markdown draw.io sections are represented by:
  `<!-- confluence-drawio diagram="..." diagram_slug="..." owner_page_id="..." source="..." -->`
- Rendered Mermaid sections are represented by:
  `<!-- confluence-drawio-rendered diagram="..." diagram_slug="..." owner_page_id="..." source="..." xml="..." -->`
- `diagram_slug` is the primary section-to-XML key.
- One heading section must contain at most one draw.io marker.
- After Mermaid rendering, replace the placeholder block with the rendered marker plus Mermaid block.
- Any unmatched or unused XML file is a hard failure.

## Rewrite Policy

- Preserve front matter.
- Preserve all heading lines and order.
- Replace only the draw.io block inside sections that contain a draw.io marker.
- Re-run safely only on sections that already contain a `confluence-drawio-rendered` marker.
