---
name: export-confluence-docs
description: Export Confluence design pages into repository `./docs` Markdown, collect linked draw.io attachments into `/tmp/export-confluence-docs`, and prepare the documents for Mermaid replacement. Use when Codex needs one end-to-end workflow for Confluence design docs that uses Atlassian MCP for page and attachment retrieval, then uses local scripts for draw.io XML mapping, diagram analysis, and Markdown section rewrite.
---

# Export Confluence Docs

Run one Confluence-to-Markdown workflow that keeps final docs in `./docs` and uses temporary draw.io XML from `/tmp/export-confluence-docs` for Mermaid conversion.

Path base: every relative path in this skill, including `scripts/...` and `references/...`, is relative to this skill directory (`/home/robo/.codex/skills/export-confluence-docs/`), not the target repository root.

## Preconditions

- Atlassian MCP must already be available in the current session.
- This skill does not install, register, or configure Atlassian MCP.
- If Atlassian MCP is unavailable, stop at the export stage and report the blocker instead of falling back to direct REST calls.

## Workflow

1. Use Atlassian MCP to find the target Confluence page by title. Prefer an exact title match and narrow by space when the workspace exposes that option.
2. Use Atlassian MCP to fetch the selected page's storage body, page id, version, and source URL.
3. Convert the page storage into repository Markdown and draw.io placeholders using the skill's local export tooling or equivalent local transformation logic.
4. Use Atlassian MCP to fetch the linked draw.io attachments for the page or referenced owner pages, then save the XML files under `/tmp/export-confluence-docs/<slug>--<page_id>/`.
5. Inspect the generated Markdown path and temporary XML directory for the conversion stage.
6. Run `python3 scripts/map_doc_drawio.py --doc <markdown-path> --xml-dir <temp-xml-dir>` to map placeholders to XML.
7. Run `python3 scripts/extract_drawio_ir.py --xml <xml-path>` for each XML file.
8. Read [references/diagram-selection.md](references/diagram-selection.md) before choosing Mermaid type.
9. Read [references/output-conventions.md](references/output-conventions.md) before writing Mermaid.
10. Build a diagram JSON payload for `scripts/render_mermaid_doc.py`.
11. Run `python3 scripts/render_mermaid_doc.py --doc <markdown-path> --diagram-json <json-path>` to replace the placeholder section body with Mermaid.

## MCP Notes

- Treat Atlassian MCP as the only supported source for page lookup and attachment download in this workflow.
- Do not tell the user to set `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`, or other direct REST credentials for this skill.
- `scripts/export_confluence_bundle.py` remains in the skill bundle, but it is not the primary entrypoint for the MCP-first workflow documented here.
- The local scripts in this skill are for mapping, extraction, and Markdown rewrite after MCP has already provided the page content and attachment files.

## Export Contract

- Final Markdown path is `docs/<slug>.md` unless your local export step intentionally writes elsewhere.
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

- Run `python3 /home/robo/.codex/skills/skill-creator/scripts/quick_validate.py /home/robo/projects/skills-workbench/skills/export-confluence-docs` after editing the skill.
- Run `python3 tests_export_confluence_docs.py` from the skill directory to validate placeholder export and mapping logic.
- Use `scripts/render_mermaid_doc.py --stdout` or `--check` before overwriting files when you want a safe preview.
