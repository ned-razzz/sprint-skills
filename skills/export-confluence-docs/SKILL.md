---
name: export-confluence-docs
description: Export Confluence design pages using the current working directory `config.json` as the source of truth for page selection and Markdown output paths, collect linked draw.io attachments into `/tmp/export-confluence-docs`, and prepare the documents for Mermaid replacement.
---

# Export Confluence Docs

Run one Confluence-to-Markdown workflow that reads the current working directory `config.json` first, uses it to decide which Confluence documents to export and where the final Markdown should be written, and keeps temporary draw.io XML in `/tmp/export-confluence-docs` for deterministic Mermaid conversion.

Path base: every relative path in this skill, including `scripts/...` and `references/...`, is relative to this skill directory (`/home/robo/.codex/skills/export-confluence-docs/`), not the target repository root.

## Preconditions

- Atlassian MCP must already be available in the current session.
- This skill does not install, register, or configure Atlassian MCP.
- If Atlassian MCP is unavailable, stop at the export stage and report the blocker.
- `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN` must already be set in the environment before any draw.io XML download step.
- Before page lookup, inspect `./config.json` from the current working directory.
- When `./config.json` exists and is valid, treat it as the single source of truth for export scope and output paths.

## Config Contract

- Read `./config.json` from the current working directory, not from the skill directory.
- Supported keys are:
  - `baseUrl`: target Confluence site root.
  - `spaceKey`: optional Confluence space filter.
  - `titles`: ordered list of page titles to export.
  - `outputDir`: final Markdown output directory.
- When `./config.json` is present and valid:
  - Use `titles` as the only page list for Atlassian MCP lookup.
  - Use `spaceKey` when narrowing page search.
  - Use `baseUrl` as the source Confluence instance.
  - Use `outputDir` to decide the final Markdown location.
  - Do not invent substitute titles, spaces, or output paths.
- When `./config.json` is missing or invalid, report that condition and you may fall back to the older manual MCP-first workflow if the task still needs to continue.

## Workflow

1. Read `./config.json` from the current working directory and determine `baseUrl`, `spaceKey`, `titles`, and `outputDir` from it when available.
2. Use Atlassian MCP to find each target Confluence page by the configured title. Prefer an exact title match and narrow by `spaceKey` when available.
3. Use Atlassian MCP to fetch each selected page's storage body, page id, version, and source URL.
4. Use Atlassian MCP to collect draw.io attachment metadata for the page or referenced owner pages, including attachment id, title, media type, and `_links.download`.
5. Convert the page storage into repository Markdown and draw.io placeholders using the skill's local export tooling or equivalent local transformation logic, writing the Markdown under `outputDir`.
6. Build absolute attachment download URLs from the MCP metadata and download the XML files into `/tmp/export-confluence-docs/<slug>--<page_id>/` with a terminal command such as `curl --fail --silent --show-error --location --user "$CONFLUENCE_EMAIL:$CONFLUENCE_API_TOKEN"`.
7. Validate that each downloaded attachment is valid XML before continuing.
8. Inspect the generated Markdown path and temporary XML directory for the conversion stage.
9. Run `python3 scripts/map_doc_drawio.py --doc <markdown-path> --xml-dir <temp-xml-dir>` and save the mapping JSON.
10. Run `python3 scripts/render_drawio_mermaid.py --map-json <map-json> --out <diagram-json>` to build deterministic Mermaid from the mapped XML files.
11. Run `python3 scripts/render_mermaid_doc.py --doc <markdown-path> --diagram-json <diagram-json>` to replace the placeholder section body with Mermaid.

## MCP Notes

- Treat Atlassian MCP as the supported source for page lookup, page body retrieval, and draw.io attachment metadata discovery in this workflow.
- Use the terminal for the actual draw.io XML download step because Atlassian MCP does not return the file bytes.
- `scripts/export_confluence_bundle.py` remains in the skill bundle, but it is not the primary entrypoint for the MCP-first workflow documented here.
- `scripts/export_confluence_bundle.py` contains reusable helpers for placeholder export, attachment matching, absolute download URL construction, and XML validation.
- The local scripts in this skill are for mapping, deterministic Mermaid generation, and Markdown rewrite after page content plus downloaded attachment files are available locally.
- The skill contract still requires that the export scope and final Markdown destination come from the current working directory `config.json` when that file is available.

## Export Contract

- Final Markdown path is `<outputDir>/<slug>.md` when `outputDir` is defined in the current working directory `config.json`.
- Temporary XML path is `/tmp/export-confluence-docs/<slug>--<page_id>/`.
- Draw.io attachment metadata comes from Atlassian MCP, but XML bytes are downloaded directly with `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN`.
- Raw draw.io sections are exported as one HTML comment placeholder per heading section:
  `<!-- confluence-drawio diagram="..." diagram_slug="..." owner_page_id="..." source="..." -->`
- `diagram_slug` is the primary XML mapping key.
- If a page has no draw.io diagrams, keep the Markdown as exported and skip Mermaid rendering.
- If one section contains multiple draw.io placeholders, stop and surface the mismatch instead of guessing.

## Rendering Rules

- Preserve system meaning, not draw.io cosmetics.
- Use the deterministic engine in `scripts/render_drawio_mermaid.py`; do not hand-write Mermaid from the XML or IR.
- The current engine supports architecture, topology, deployment, and component diagrams only.
- The current engine always emits `flowchart TB`.
- Preserve important edge labels such as `TCP`, `HTTP`, `ROS`, and `UDP`.
- Preserve front matter and headings.
- Replace only the body of placeholder sections.
- Remove the `confluence-drawio` placeholder after rendering Mermaid.
- If the deterministic engine cannot safely preserve the meaning of a diagram, fail the document instead of guessing.

## References

- Read [references/repo-workflow.md](references/repo-workflow.md) for path and placeholder rules.
- Read [references/diagram-selection.md](references/diagram-selection.md) for supported diagram scope and failure conditions.
- Read [references/output-conventions.md](references/output-conventions.md) for deterministic Mermaid output rules.

## Validation

- Run `python3 /home/robo/.codex/skills/.system/skill-creator/scripts/quick_validate.py /home/robo/projects/skills-workbench/skills/export-confluence-docs` after editing the skill.
- Run `python3 tests_export_confluence_docs.py` from the skill directory to validate placeholder export and mapping logic.
- Use `scripts/render_mermaid_doc.py --stdout` or `--check` before overwriting files when you want a safe preview.
