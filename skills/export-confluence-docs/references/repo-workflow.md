# Repository Workflow

Use this skill with repositories that want final Markdown in `./docs` and temporary draw.io XML in `/tmp/export-confluence-docs`.

Assume the page body and draw.io attachments were retrieved through Atlassian MCP before these repository layout rules are applied.

## Export Layout

- Markdown documents live at `docs/*.md`.
- Each exported document includes `confluence_page_id` in YAML front matter.
- Temporary XML lives at `/tmp/export-confluence-docs/<markdown-stem>--<confluence_page_id>/`.
- XML files come from draw.io attachments downloaded through Atlassian MCP.
- XML files are normalized to `<diagram-slug>.xml` when the Confluence macro exposes `diagramName`.

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
