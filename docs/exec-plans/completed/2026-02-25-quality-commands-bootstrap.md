# 2026-02-25-quality-commands-bootstrap: unified local quality commands

## Context

Repository now has docs-first scaffolding, but quality commands are not standardized yet.
We need a single predictable command interface for humans and agents.

## Scope

- In scope:
  - Add baseline quality commands to `justfile`
  - Document those commands in `README.md`
- Out of scope:
  - CI workflow changes
  - strict architecture import graph checks

## Plan

1. [x] Add `test`, `test-fast`, `lint`, `fmt`, `check`, `arch-test` recipes in `justfile`
2. [x] Keep docker recipes intact
3. [x] Document quality commands in `README.md`
4. [x] Run local verification (`just --list`, `just check`, `just arch-test`)
5. [x] Move this plan to `completed/` after merge

## Risks and Open Questions

- `fmt` and `lint` are bootstrap-level and intentionally conservative.
- `arch-test` verifies docs/structure now; import-boundary checks will be added later.

## Verification

- `just --list`
- `just check`
- `just arch-test`

## Definition of Done

- [x] Commands are available and documented
- [x] Verification commands pass locally
- [x] No behavior/runtime code changed
