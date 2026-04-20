---
name: pull-docs-from-confluence
description: Pull selected Confluence pages into repository Markdown files based on the current working directory `config.json`, including diagram-ready document output.
---

# When to use

Use this skill when the repository's current working directory contains a `./config.json` that defines which Confluence pages to export and where the final Markdown should be written.

Use this skill for the sequential REST workflow:

- Step 1 checks the local environment: `./config.json`, Atlassian MCP availability, and `CONFLUENCE_EMAIL` plus `CONFLUENCE_API_TOKEN`.
- Step 2 runs `python3 scripts/fetch_confluence_metatdata.py --config ./config.json` to resolve selected pages and write `./bundle.json`.
- Step 3 runs `python3 scripts/export_confluence_assets.py --config ./config.json --bundle ./bundle.json` to export Markdown drafts and draw.io XML.
- Step 4 runs `python3 scripts/render_drawio_to_mermaid.py --doc <markdownPath>` for each Markdown file produced in step 3.
- Step 5 verifies that the whole workflow completed with no failed pages and no pending Mermaid rewrites.

Read reference files only when the current task needs details that are not already explicit in this SKILL.md.

- [references/repo-workflow.md](references/repo-workflow.md): Read when output paths, placeholder rules, front matter preservation, or rewrite boundaries need clarification.
- [references/diagram-selection.md](references/diagram-selection.md): Read when supported diagram scope or fail-instead-of-guessing rules need clarification.
- [references/output-conventions.md](references/output-conventions.md): Read when Mermaid node naming, grouping, labeling, or rendered block formatting rules need clarification.

# Inputs

Read `./config.json` from the current working directory, not from the skill directory.

Expected `./config.json` shape:

```json
{
  "siteUrl": "https://<site>.atlassian.net/",
  "titles": ["Page Title A", "Page Title B"],
  "spaceKey": "OPTIONAL",
  "outputDir": "docs/confluence"
}
```

Input contract:

- `siteUrl`: required Atlassian site root URL used by the REST scripts.
- `titles`: required ordered array of page titles to export. Treat this as the full export scope.
- `spaceKey`: optional Confluence space filter for page lookup.
- `outputDir`: required destination directory for final Markdown files.
- Do not invent substitute titles, spaces, site URLs, or output paths when `./config.json` is present.

# Preconditions

- Atlassian MCP must already be available in the current session.
- This skill does not install, register, or configure Atlassian MCP.
- `./config.json` must exist in the current working directory and be valid JSON.
- `siteUrl` in `./config.json` must be a valid Atlassian site root URL.
- `titles` must be present in `./config.json`.
- `titles` must contain at least one page title.
- `outputDir` must be present in `./config.json`.
- `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN` must already be set before any metadata fetch, asset export, or draw.io XML download step.

# Output

- Step 2 writes `./bundle.json` in the current working directory.
- Step 3 writes final Markdown drafts to `<outputDir>/<slug>.md`.
- Step 3 writes temporary XML to `/tmp/export-confluence-docs/<slug>--<page_id>/`.
- Each exported Markdown document should preserve front matter and headings.
- Each exported Markdown document should include `confluence_page_id` in YAML front matter.
- Raw draw.io sections are represented by:
  `<!-- confluence-drawio diagram_slug="..." owner_page_id="..." source="..." -->`
- `diagram_slug` is the primary key for matching section markers to XML files.
- Pages without draw.io diagrams should remain plain Markdown exports with no Mermaid conversion.

# Steps

1. Check the environment before running any export command:
   - confirm `./config.json` exists and includes `titles`, `spaceKey`, and `outputDir`
   - confirm Atlassian MCP is reachable in the current session
   - confirm `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN` are set
2. Run `python3 scripts/fetch_confluence_metatdata.py --config ./config.json`.
   - This script uses Confluence REST, resolves exactly one selected page for each configured title, collects page storage and attachment metadata, and writes `./bundle.json`.
   - Treat `./bundle.json` as the only bundle input for the next step.
3. Run `python3 scripts/export_confluence_assets.py --config ./config.json --bundle ./bundle.json`.
   - This script exports Markdown placeholders into `outputDir`, downloads draw.io XML with `curl` into `/tmp/export-confluence-docs/<slug>--<page_id>/`, and prints a JSON summary.
   - Use `results[].markdownPath` from that summary as the document list for the next step.
4. For each Markdown file produced in step 3, run `python3 scripts/render_drawio_to_mermaid.py --doc <markdownPath>`.
   - Use the default XML directory unless a custom `--xml-dir` is required.
   - Pages with no draw.io XML may remain unchanged; that is still a successful render pass.
5. Verify completion.
   - Step 3 summary must report `failed = 0`.
   - Every configured title must produce one Markdown file at `<outputDir>/<slug>.md`.
   - Re-run `python3 scripts/render_drawio_to_mermaid.py --doc <markdownPath> --check` for each generated document when a final no-op verification is needed.

# Success criteria

- Every title in `./config.json` resolves to exactly one selected Confluence page.
- Step 2 writes exactly one `./bundle.json` for the configured export scope.
- Every selected page produces exactly one Markdown file at `<outputDir>/<slug>.md`.
- Pages without draw.io diagrams remain valid Markdown exports.
- Pages with supported draw.io diagrams replace placeholder section bodies with Mermaid blocks.
- No XML file remains unmatched or ambiguously matched to section markers.
- The final documents preserve source meaning, front matter, and heading structure.
- The workflow completes without inventing missing data or hand-writing Mermaid from XML.

# Failure handling

- If Atlassian MCP is unavailable, stop and report the blocker during step 1.
- If `./config.json` is missing, invalid, or missing required keys, stop and report the configuration error during step 1.
- If `titles` is empty, stop and report that the export scope is empty.
- If `CONFLUENCE_EMAIL` or `CONFLUENCE_API_TOKEN` is missing, stop and report the missing credential during step 1.
- If step 2 fails to resolve a configured title to exactly one page, stop and report the ambiguous or missing match.
- If step 3 fails for any configured title, do not claim workflow success.
- If one section contains multiple draw.io markers, stop and report the mismatch instead of guessing.
- If any XML file cannot be matched to exactly one section marker, stop and report the mismatch instead of guessing.
- If the deterministic renderer cannot safely preserve the meaning of a diagram, fail that document instead of guessing.
- Do not hand-write Mermaid, do not substitute different pages or paths, and do not claim success when any configured page failed.
