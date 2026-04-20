# Overview
This repository provides specialized skills for the Gemini CLI to automate and streamline technical documentation workflows. The primary focus is on synchronizing documentation from Confluence into local Markdown files, ensuring that both text and complex architectural diagrams via draw.io are accurately preserved and converted into version-control-friendly formats like Mermaid.

# Skill List
- **pull-docs-from-confluence**: Synchronizes selected Confluence pages into repository Markdown files. It uses a deterministic rendering engine that converts draw.io diagrams into Mermaid flowcharts so technical diagrams can be maintained as code alongside the documentation.

# Prerequisites
## pull-docs-from-confluence 
Before using the `pull-docs-from-confluence` skill, ensure your environment meets the following requirements:

- **Confluence Access**: Atlassian MCP must be available for environment validation, and the REST workflow uses `siteUrl` from `config.json`.
- **Environment Variables**: The following credentials are required for metadata fetches, asset export, and draw.io XML downloads:
  ```bash
  export CONFLUENCE_EMAIL="your-email@example.com"
  export CONFLUENCE_API_TOKEN="your-atlassian-api-token"
  ```
- **System Dependencies**:
  - `python3`: Required to run the export and rendering scripts.
  - `curl`: Required for authenticated attachment downloads.
- **Python Packages**: Install the necessary library dependencies:
  ```bash
  pip install requests lxml
  ```
- **Project Configuration**: A `config.json` file must exist in your current working directory to define the export scope:
  ```json
  {
    "siteUrl": "https://example.atlassian.net/",
    "titles": ["Page Title A", "Page Title B"],
    "spaceKey": "OPTIONAL",
    "outputDir": "docs/confluence"
  }
  ```

# Workflow
## pull-docs-from-confluence

1. Check the environment: `config.json`, Atlassian MCP, and `CONFLUENCE_EMAIL` plus `CONFLUENCE_API_TOKEN`.
2. Run `python3 scripts/fetch_confluence_metatdata.py --config ./config.json`.
3. Run `python3 scripts/export_confluence_assets.py --config ./config.json --bundle-json ./bundle.json`.
4. Run `python3 scripts/render_drawio_to_mermaid.py --doc <markdownPath>` for each Markdown file reported by step 3.
5. Verify that step 3 reports zero failed pages and that an optional final `--check` run is clean for every generated document.
