# Diagram Scope

The deterministic engine supports architecture-style draw.io documents only.

## Supported Inputs

- System architecture
- Hardware layout
- Deployment topology
- Components and communication links
- Services, devices, databases, and controllers connected by labeled or unlabeled edges

## Output Type

- Always emit `flowchart TB`.
- Do not switch to `sequenceDiagram`, `stateDiagram-v2`, or `classDiagram`.

## Fail Instead Of Guessing

Fail the document when the XML contains meaning that cannot be safely preserved as an architecture flowchart, including:

- Connected unlabeled vertices
- Edges that point at non-renderable vertices
- Text-only nodes that participate in edges
- Overlapping container semantics that are not strictly nested
- Explicitly state/class-like labels such as `[*]` or `<<...>>`

## Repository Guidance

- `Hardware Architecture` should render as `flowchart TB`.
- `Software Architecture` should render as `flowchart TB`.
- Preserve communication labels such as `TCP`, `HTTP`, `ROS`, and `UDP`.
