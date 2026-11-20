# Scoring notes (demo): specialises prompts/SCORING.md

## Role shape: build vs operate

- **Up (BUILD):** build, design, develop, architect, automate, own the platform,
  write operators/controllers/tooling.
- **Down (OPERATE):** maintain, monitor, respond, triage, on-call, support.

| Dominant ratio | Band |
|---|---|
| Developer platform / IDP or operator ownership | 30-40 |
| Infra / Kubernetes build | 18-28 |
| SRE / release / CI-CD mixed | 10-18 |
| Ticket-driven or on-call-dominant | 0 |

Domain test: SWE-titled roles centred on platforms or devtools are in; product
business-logic backends are `drop` (the code flags `POSSIBLE-APP-DEV`).

## Differentiators worth quoting

Kubernetes operators/controllers, Go, GitOps/Argo CD, internal developer platforms.

**False positives:** `operator` can mean a human operator.

## Caps

- 70: a deep-AWS requirement (`SHALLOW-SKILL-DEMAND: aws`).
- 65: on-call-dominant ops.

## Deep-dive artifacts

Self-service deployment platform, namespace operator, Helm library chart, Actions
migration.
