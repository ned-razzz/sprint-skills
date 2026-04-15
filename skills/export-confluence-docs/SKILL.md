---
name: export-confluence-docs
description: Export Confluence design pages into repository `./docs` Markdown, export linked draw.io attachments into `/tmp/export-confluence-docs`, and prepare the documents for Mermaid replacement. Use when Codex needs one end-to-end workflow for Confluence design docs that includes page export, draw.io XML export, diagram analysis, and Markdown section rewrite.
---

# Export Confluence Docs

Run one Confluence-to-Markdown workflow that keeps final docs in `./docs` and uses temporary draw.io XML from `/tmp/export-confluence-docs` for Mermaid conversion.

Path base: every relative path in this skill, including `scripts/...` and `references/...`, is relative to this skill directory (`/home/robo/.codex/skills/export-confluence-docs/`), not the target repository root.

## Workflow

1. Require `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN`.
2. Prepare a config JSON with `baseUrl`, `titles`, optional `spaceKey`, and optional `outputDir`.
3. Run `python3 scripts/export_confluence_bundle.py --config <path-or->`.
4. Inspect the manifest JSON from stdout. Use each result's `markdownPath` and `tempXmlDir` for the conversion stage.
5. Run `python3 scripts/map_doc_drawio.py --doc <markdown-path> --xml-dir <temp-xml-dir>` to map placeholders to XML.
6. Run `python3 scripts/extract_drawio_ir.py --xml <xml-path>` for each XML file.
7. Read [references/diagram-selection.md](references/diagram-selection.md) before choosing Mermaid type.
8. Read [references/output-conventions.md](references/output-conventions.md) before writing Mermaid.
9. Build a diagram JSON payload for `scripts/render_mermaid_doc.py`.
10. Run `python3 scripts/render_mermaid_doc.py --doc <markdown-path> --diagram-json <json-path>` to replace the placeholder section body with Mermaid.

## Export Contract

- Final Markdown path is `docs/<slug>.md` unless config overrides `outputDir`.
- Temporary XML path is `/tmp/export-confluence-docs/<slug>--<page_id>/`.
- Raw draw.io sections are exported as one HTML comment placeholder per heading section:
  `<!-- confluence-drawio diagram="..." diagram_slug="..." owner_page_id="..." source="..." -->`
- `diagram_slug` is the primary XML mapping key.
- If a page has no draw.io diagrams, keep the Markdown as exported and skip Mermaid rendering.
- If one section contains multiple draw.io placeholders, stop and surface the mismatch instead of guessing.

## Rendering Rules

- Preserve system meaning, not draw.io cosmetics.
- Prefer `flowchart TB` unless the IR clearly indicates sequence, state, or class semantics.
- Preserve important edge labels such as `TCP`, `HTTP`, `ROS`, and `UDP`.
- Preserve front matter and headings.
- Replace only the body of placeholder sections.
- Remove the `confluence-drawio` placeholder after rendering Mermaid.

## References

- Read [references/repo-workflow.md](references/repo-workflow.md) for path and placeholder rules.
- Read [references/diagram-selection.md](references/diagram-selection.md) for Mermaid type choice.
- Read [references/output-conventions.md](references/output-conventions.md) for Mermaid output rules.

## Validation

- Run `python3 /home/robo/.codex/skills/skill-creator/scripts/quick_validate.py /home/robo/.codex/skills/export-confluence-docs` after editing the skill.
- Run `python3 tests_export_confluence_docs.py` from the skill directory to validate placeholder export and mapping logic.
- Use `scripts/render_mermaid_doc.py --stdout` or `--check` before overwriting files when you want a safe preview.
