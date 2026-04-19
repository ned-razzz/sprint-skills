# Overview
This repository provides specialized skills for the Gemini CLI to automate and streamline technical documentation workflows. The primary focus is on synchronizing documentation from Confluence into local Markdown files, ensuring that both text and complex architectural diagrams (via draw.io) are accurately preserved and converted into version-control-friendly formats like Mermaid.

# Skill List
- **pull-docs-from-confluence**: Synchronizes selected Confluence pages into repository Markdown files. It features a deterministic rendering engine that converts draw.io diagrams into Mermaid flowcharts, allowing technical diagrams to be maintained as code alongside the documentation.

# pull-docs-from-confluence Installation

## Prerequisites
Before using the `pull-docs-from-confluence` skill, ensure your environment meets the following requirements:

- **Confluence Access**: The canonical export path fetches page metadata and content through the Confluence REST API. Atlassian MCP is optional when you already have a prebuilt bundle for `run_mcp_export.py`.
- **Environment Variables**: The following credentials are required to download draw.io XML attachments:
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
    "titles": ["Page Title A", "Page Title B"],
    "outputDir": "docs/confluence",
    "spaceKey": "OPTIONAL"
  }
  ```

## pull-docs-from-confluence Workflow

- Canonical path: `python3 scripts/export_confluence_bundle.py --site-url https://<site>.atlassian.net --config ./config.json`
- Compatibility path: `python3 scripts/run_mcp_export.py --config ./config.json --bundle-json <bundle.json>`
- Both paths share the same bundle processor for Markdown export, draw.io XML download, mapping, and Mermaid rendering.
