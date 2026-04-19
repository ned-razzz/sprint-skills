---
name: pull-docs-from-confluence
description: Pull selected Confluence pages into repository Markdown files based on the current working directory `config.json`, including diagram-ready document output.
---

# When to use

Use this skill when the repository's current working directory contains a `./config.json` that defines which Confluence pages to export and where the final Markdown should be written.

Use this skill only for the MCP-first workflow:

- Atlassian MCP finds pages and attachment metadata.
- `python3 scripts/run_mcp_export.py --config ./config.json --bundle-json <bundle-json>` performs export and rewrite.
- `curl` downloads draw.io XML because Atlassian MCP does not return attachment bytes.

Read reference files only when the current task needs details that are not already explicit in this SKILL.md.

- [references/repo-workflow.md](references/repo-workflow.md): Read when output paths, placeholder rules, front matter preservation, or rewrite boundaries need clarification.
- [references/diagram-selection.md](references/diagram-selection.md): Read when supported diagram scope or fail-instead-of-guessing rules need clarification.
- [references/output-conventions.md](references/output-conventions.md): Read when Mermaid node naming, grouping, labeling, or rendered block formatting rules need clarification.

# Inputs

Read `./config.json` from the current working directory, not from the skill directory.

Expected `./config.json` shape:

```json
{
  "baseUrl": "https://<site>.atlassian.net",
  "spaceKey": "OPTIONAL",
  "titles": ["Page Title A", "Page Title B"],
  "outputDir": "docs/confluence"
}
```

Input contract:

- `baseUrl`: required Confluence site root.
- `spaceKey`: optional Confluence space filter for page lookup.
- `titles`: required ordered array of page titles to export. Treat this as the full export scope.
- `outputDir`: required destination directory for final Markdown files.
- Do not invent substitute titles, spaces, or output paths when `./config.json` is present.

The script input to `scripts/run_mcp_export.py` is a local bundle JSON assembled from Atlassian MCP output. That bundle must include, for each selected page, the page body plus enough page and attachment metadata for the runner to download and map draw.io XML:

- page title, page id, version, and source URL
- storage body
- draw.io attachment metadata including attachment id, title, media type, owner page id, and `_links.download`

# Preconditions

- Atlassian MCP must already be available in the current session.
- This skill does not install, register, or configure Atlassian MCP.
- `./config.json` must exist in the current working directory and be valid JSON.
- `baseUrl`, `titles`, and `outputDir` must be present in `./config.json`.
- `titles` must contain at least one page title.
- `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN` must already be set before any draw.io XML download step.

# Output

- Final Markdown path: `<outputDir>/<slug>.md`
- Temporary XML path: `/tmp/export-confluence-docs/<slug>--<page_id>/`
- Each exported Markdown document should preserve front matter and headings.
- Each exported Markdown document should include `confluence_page_id` in YAML front matter.
- Raw draw.io sections are represented by:
  `<!-- confluence-drawio diagram="..." diagram_slug="..." owner_page_id="..." source="..." -->`
- Rendered Mermaid sections are represented by:
  `<!-- confluence-drawio-rendered diagram="..." diagram_slug="..." owner_page_id="..." source="..." xml="..." -->`
- `diagram_slug` is the primary key for matching section markers to XML files.
- Pages without draw.io diagrams should remain plain Markdown exports with no Mermaid conversion.

# Steps

1. Read `./config.json` from the current working directory and use it as the single source of truth for `baseUrl`, `spaceKey`, `titles`, and `outputDir`.
2. Use Atlassian MCP to find exactly one Confluence page for each configured title. Prefer exact title matches and narrow by `spaceKey` when provided.
3. Use Atlassian MCP to fetch each selected page's storage body, page id, version, and source URL.
4. Use Atlassian MCP to collect draw.io attachment metadata for each selected page or referenced owner page.
5. Assemble the MCP results into one local bundle JSON.
6. Run `python3 scripts/run_mcp_export.py --config ./config.json --bundle-json <bundle-json>`.
7. Let the runner export Markdown placeholders into `outputDir`, download draw.io XML with `curl` into `/tmp/export-confluence-docs/<slug>--<page_id>/`, validate XML, map XML to section markers, render Mermaid with `scripts/render_drawio_mermaid.py`, and rewrite the Markdown document.
8. Use `scripts/render_mermaid_doc.py --stdout` or `--check` before overwriting files when a safe preview is needed.

# Success criteria

- Every title in `./config.json` resolves to exactly one selected Confluence page.
- Every selected page produces exactly one Markdown file at `<outputDir>/<slug>.md`.
- Pages without draw.io diagrams remain valid Markdown exports.
- Pages with supported draw.io diagrams replace placeholder section bodies with a `confluence-drawio-rendered` marker followed by a Mermaid block.
- No XML file remains unmatched or ambiguously matched to section markers.
- The final documents preserve source meaning, front matter, and heading structure.
- The workflow completes without inventing missing data or hand-writing Mermaid from XML.

# Failure handling

- If Atlassian MCP is unavailable, stop and report the blocker.
- If `./config.json` is missing, invalid, or missing required keys, stop and report the configuration error.
- If `titles` is empty, stop and report that the export scope is empty.
- If a configured title does not resolve to exactly one page, stop and report the ambiguous or missing match.
- If draw.io XML download is required but `CONFLUENCE_EMAIL` or `CONFLUENCE_API_TOKEN` is missing, stop and report the missing credential.
- If one section contains multiple draw.io markers, stop and report the mismatch instead of guessing.
- If any XML file cannot be matched to exactly one section marker, stop and report the mismatch instead of guessing.
- If the deterministic renderer cannot safely preserve the meaning of a diagram, fail that document instead of guessing.
- Do not hand-write Mermaid, do not substitute different pages or paths, and do not claim success when any configured page failed.
