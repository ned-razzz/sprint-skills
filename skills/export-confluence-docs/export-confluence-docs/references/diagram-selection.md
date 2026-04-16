# Diagram Selection

Use the IR to choose the Mermaid type that best represents the diagram's meaning.

## Prefer `flowchart TB`

Choose `flowchart TB` when the XML describes:

- System architecture
- Hardware layout
- Deployment topology
- Components and communication links
- Services, devices, databases, and controllers connected by labeled edges

This is the default and the fallback.

## Choose `sequenceDiagram`

Choose `sequenceDiagram` only when:

- The main meaning is temporal interaction
- Participants are stable actors or services
- Edge order matters more than containment or topology

Do not choose it just because arrows exist.

## Choose `stateDiagram-v2`

Choose `stateDiagram-v2` only when:

- Nodes are states rather than components
- Edges represent explicit transitions
- Labels read like triggers, events, or conditions

## Choose `classDiagram`

Choose `classDiagram` only when:

- Nodes are types, classes, or entities
- Edges represent inheritance, composition, ownership, or typed associations
- Methods or attributes are present or strongly implied

## Repository Guidance

For the current repository:

- `Hardware Architecture` should normally render as `flowchart TB`.
- `Software Architecture` should normally render as `flowchart TB`.
- Preserve communication labels such as `TCP`, `HTTP`, `ROS`, and `UDP`.
