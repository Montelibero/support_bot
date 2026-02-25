# Agent Navigation Index

This file is a short navigation map for AI and human contributors.
It is intentionally concise. Detailed project rules live in `docs/`.

Temporary policy reference: `AI_FIRST.md`.

If there is any conflict between files, use this priority:
1. `docs/*`
2. `AGENTS.md`
3. `AI_FIRST.md` (reference only during migration)

## Reading Order

1. `README.md` — project overview and startup
2. `AGENTS.md` — where to find source-of-truth docs
3. `docs/architecture.md` — current and target architecture
4. `docs/conventions.md` — coding and delivery conventions
5. `docs/golden-principles.md` — non-negotiable invariants
6. `docs/glossary.md` — project language
7. `adr/` — decision history

## Core Documentation

- `docs/architecture.md`: boundaries, layers, migration direction
- `docs/conventions.md`: coding style, testing, PR hygiene
- `docs/golden-principles.md`: architectural axioms
- `docs/glossary.md`: canonical domain terms

## Plans and Execution

- Active plans: `docs/exec-plans/active/`
- Completed plans: `docs/exec-plans/completed/`
- Template: `docs/exec-plans/active/_template.md`

Use an execution plan for any non-trivial change.

## ADRs

- ADR index and template: `adr/README.md`

Create an ADR when changing architecture, public contracts, or major dependencies.

## Runbooks

- `docs/runbooks/README.md` contains runbook format and index.

## Linters and Guardrails

- `.linters/README.md` defines structural checks roadmap.

## Migration Notes

This repository is moving toward a stronger docs-first and agent-first workflow.
During migration:

- Keep diffs small and incremental.
- Prefer explicit contracts and tests.
- Update docs next to code changes.

## Definition of Ready for Implementation

Before coding a non-trivial task:

- Problem is described in an execution plan.
- Affected files and risks are listed.
- Verification steps are explicit.

## Definition of Done for Changes

- Code changes are minimal and scoped.
- Relevant tests are added/updated and pass.
- Docs are updated if contracts/behavior changed.
- Plan is moved from `active/` to `completed/` when done.
