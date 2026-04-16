# Output Conventions

## Node Identity

- Use slug-like Mermaid ids based on normalized labels.
- Add numeric suffixes for duplicate labels such as `camera_2`.
- Keep labels human-readable.

## Grouping

- Convert meaningful enclosing boxes to `subgraph`.
- Do not create `subgraph` for decorative category text alone.
- Keep nested groups only when they express real containment.

## Labels and Edges

- Preserve edge labels that represent protocol, transport, or action.
- Keep unlabeled structural edges simple.
- Avoid introducing labels that are not supported by the IR.

## Decorative Elements

- Drop layout-only text like broad column labels unless they carry meaning that would otherwise be lost.
- Drop styling artifacts, font choices, and exact positions.
- Preserve device, service, database, and controller distinctions when possible.

## Markdown Rendering

- Emit fenced code blocks with `mermaid`.
- Keep only Mermaid source inside the fence.
- End each replaced section body with a newline.
