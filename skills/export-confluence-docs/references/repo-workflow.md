# Repository Workflow

Use this skill with repositories that provide a current working directory `config.json` describing the Confluence titles to export and the final Markdown output directory.

Assume the page body was retrieved through Atlassian MCP and the draw.io attachment bytes were downloaded locally from attachment metadata before these repository layout rules are applied.

## Export Layout

- Read `./config.json` from the current working directory before deciding export targets.
- When `config.json` is present and valid, export only the page titles listed in `titles`.
- Final Markdown documents live at `<outputDir>/*.md`, where `outputDir` comes from `config.json`.
- Each exported document includes `confluence_page_id` in YAML front matter.
- Temporary XML lives at `/tmp/export-confluence-docs/<markdown-stem>--<confluence_page_id>/`.
- XML files come from draw.io attachments discovered through Atlassian MCP metadata and downloaded directly with Confluence credentials.
- XML files are normalized to `<diagram-slug>.xml` when the Confluence macro exposes `diagramName`.
- If `config.json` is missing or invalid, surface that condition before using any fallback manual export flow.

## Placeholder Contract

- Raw Markdown draw.io sections are represented by:
  `<!-- confluence-drawio diagram="..." diagram_slug="..." owner_page_id="..." source="..." -->`
- `diagram_slug` is the primary section-to-XML key.
- One heading section must contain at most one draw.io placeholder.
- After Mermaid rendering, the placeholder must be removed from the section body.

## Rewrite Policy

- Preserve front matter.
- Preserve all heading lines and order.
- Replace only the body of sections that contain a draw.io placeholder.
- Re-run safely on already-rendered Mermaid sections when XML count still matches.
