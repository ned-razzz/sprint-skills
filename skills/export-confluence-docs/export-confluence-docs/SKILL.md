---
name: export-confluence-docs
description: Export Confluence design pages using the current working directory `config.json` as the source of truth for page selection and Markdown output paths, collect linked draw.io attachments into `/tmp/export-confluence-docs`, and prepare the documents for Mermaid replacement. Use when Confluence pages may contain draw.io macros that are not preserved in Markdown responses and the workflow needs ADF-aware export plus local attachment staging.
---

# Export Confluence Docs

Run one Confluence-to-Markdown workflow that reads the current working directory `config.json` first, uses it to decide which Confluence documents to export and where the final Markdown should be written, and keeps temporary draw.io XML in `/tmp/export-confluence-docs` for Mermaid conversion.

Path base: every relative path in this skill, including `scripts/...` and `references/...`, is relative to this skill directory (`/home/robo/.codex/skills/export-confluence-docs/`), not the target repository root.

## Preconditions

- Atlassian MCP must already be available in the current session.
- This skill does not install, register, or configure Atlassian MCP.
- If Atlassian MCP is unavailable, stop and report the blocker instead of guessing page ids or titles.
- When the local export script runs, it resolves the Confluence site from `CONFLUENCE_BASE_URL`.
- Before page lookup, inspect `./config.json` from the current working directory.
- When `./config.json` exists and is valid, treat it as the single source of truth for export scope and output paths.
- Expect Confluence Markdown responses to omit draw.io macros. Do not assume `contentFormat="markdown"` is sufficient to discover diagrams.
- Expect attachment export to require outbound network access when `scripts/export_confluence_bundle.py` is used.

## Config Contract

- Read `./config.json` from the current working directory, not from the skill directory.
- Supported keys are:
  - `spaceKey`: optional Confluence space filter.
  - `titles`: ordered list of page titles to export.
  - `outputDir`: final Markdown output directory.
- When `./config.json` is present and valid:
  - Use `titles` as the only page list for Atlassian MCP lookup.
  - Use `spaceKey` when narrowing page search.
  - Use `outputDir` to decide the final Markdown location.
  - Do not invent substitute titles, spaces, or output paths.
- When `./config.json` is missing or invalid, report that condition and you may fall back to the older manual MCP-first workflow if the task still needs to continue.

## Workflow

1. Read `./config.json` from the current working directory and determine `spaceKey`, `titles`, and `outputDir` from it when available.
2. Use Atlassian MCP to find each target Confluence page by the configured title. Prefer an exact title match and narrow by `spaceKey` when available.
3. Use Atlassian MCP to fetch each selected page in `adf` format first. Extract page id, version, source URL, and draw.io macro metadata such as `diagramName`, `diagram_slug`, `owner_page_id`, `custContentId`, and any embedded owner-page references.
4. Treat `contentFormat="markdown"` as preview-only. If the Markdown body omits draw.io macros or placeholder context, do not continue with Markdown-only export.
5. Prefer the bundled export path for real output generation: run `python3 scripts/export_confluence_bundle.py --config <config-path>` so one export pass writes repository Markdown under `outputDir` and stages draw.io XML under `/tmp/export-confluence-docs/<slug>--<page_id>/`.
6. Before running the bundled export script, ensure `CONFLUENCE_BASE_URL` points at the same Confluence site you inspected through Atlassian MCP.
7. If the export script fails with name resolution, connection, or sandboxed network errors, rerun it with escalated network permission instead of switching to a different unsupported workflow.
8. Inspect the generated Markdown path and temporary XML directory for the conversion stage.
9. Run `python3 scripts/map_doc_drawio.py --doc <markdown-path> --xml-dir <temp-xml-dir>` to map placeholders to XML.
10. If the document has draw.io placeholders but no heading sections, rely on the bundled scripts to add a deterministic title heading before Mermaid rendering.
11. Run `python3 scripts/extract_drawio_ir.py --xml <xml-path>` for each XML file.
12. Read [references/diagram-selection.md](references/diagram-selection.md) before choosing Mermaid type.
13. Read [references/output-conventions.md](references/output-conventions.md) before writing Mermaid.
14. Build a diagram JSON payload for `scripts/render_mermaid_doc.py`.
15. Run `python3 scripts/render_mermaid_doc.py --doc <markdown-path> --diagram-json <json-path>` to replace the placeholder section body with Mermaid.

## MCP Notes

- Treat Atlassian MCP as the required discovery source for page lookup, title matching, and ADF inspection.
- Do not expect `config.json` to choose the Confluence hostname.
- Keep the Atlassian MCP site you inspect and the `CONFLUENCE_BASE_URL` used by `scripts/export_confluence_bundle.py` aligned to the same Confluence site.
- Do not assume the available Atlassian MCP toolset can download draw.io attachments directly. If the current session lacks a working attachment-download tool, use `scripts/export_confluence_bundle.py` for the export stage.
- Do not rely on `contentFormat="markdown"` to preserve draw.io macros. Use ADF to confirm whether draw.io content exists before deciding the next step.
- Do not tell the user to set `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`, or other direct REST credentials for this skill.
- `scripts/export_confluence_bundle.py` is the primary export entrypoint once config validation and MCP page discovery are complete.
- The local scripts in this skill cover export, mapping, extraction, and Markdown rewrite.
- The skill contract still requires that the export scope and final Markdown destination come from the current working directory `config.json` when that file is available.

## Export Contract

- Final Markdown path is `<outputDir>/<slug>.md` when `outputDir` is defined in the current working directory `config.json`.
- Temporary XML path is `/tmp/export-confluence-docs/<slug>--<page_id>/`.
- Raw draw.io sections are exported as one HTML comment placeholder per heading section:
  `<!-- confluence-drawio diagram="..." diagram_slug="..." owner_page_id="..." source="..." -->`
- `diagram_slug` is the primary XML mapping key.
- If a page has no draw.io diagrams, keep the Markdown as exported and skip Mermaid rendering.
- If one section contains multiple draw.io placeholders, stop and surface the mismatch instead of guessing.
- If the exported Markdown contains a top-level draw.io placeholder without any heading, insert one deterministic heading before Mermaid rendering instead of leaving the file in an unmappable state.

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

- Run `python3 /home/robo/.codex/skills/.system/skill-creator/scripts/quick_validate.py /home/robo/.codex/skills/export-confluence-docs` after editing the skill.
- Run `python3 tests_export_confluence_docs.py` from the skill directory to validate placeholder export and mapping logic.
- Use `scripts/render_mermaid_doc.py --stdout` or `--check` before overwriting files when you want a safe preview.
