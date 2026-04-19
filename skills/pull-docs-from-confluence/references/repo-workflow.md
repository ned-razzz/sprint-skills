# Repository Workflow Details

Read this file only when repository-specific export layout or rewrite boundaries need clarification beyond `SKILL.md`.

Assume the primary workflow contract already comes from `SKILL.md`:

- `./config.json` in the current working directory is required.
- `baseUrl`, `titles`, and `outputDir` are required.
- `spaceKey` is optional.
- `titles` defines the full export scope.

This document only clarifies how the repository layout and rewrite markers behave after Atlassian MCP output has been assembled into the bundle JSON passed to `scripts/run_mcp_export.py`.

## Export Layout Details

- Read `./config.json` from the current working directory before deciding export targets.
- Export only the page titles listed in `titles`.
- Final Markdown documents live at `<outputDir>/<slug>.md`.
- Each exported document includes `confluence_page_id` in YAML front matter.
- Temporary XML lives at `/tmp/export-confluence-docs/<slug>--<page_id>/`.
- XML files come from draw.io attachments discovered through Atlassian MCP metadata and downloaded only with `curl` plus Confluence credentials.
- XML files are normalized to `<diagram-slug>.xml` when the Confluence macro exposes `diagramName`.

## Placeholder Details

- Raw Markdown draw.io sections are represented by:
  `<!-- confluence-drawio diagram="..." diagram_slug="..." owner_page_id="..." source="..." -->`
- Rendered Mermaid sections are represented by:
  `<!-- confluence-drawio-rendered diagram="..." diagram_slug="..." owner_page_id="..." source="..." xml="..." -->`
- `diagram_slug` is the primary section-to-XML key.
- One heading section must contain at most one draw.io marker.
- After Mermaid rendering, replace the placeholder block with the rendered marker plus Mermaid block.
- Any unmatched or unused XML file is a hard failure.

## Rewrite Boundaries

- Preserve front matter.
- Preserve all heading lines and order.
- Replace only the draw.io block inside sections that contain a draw.io marker.
- Re-run safely only on sections that already contain a `confluence-drawio-rendered` marker.
