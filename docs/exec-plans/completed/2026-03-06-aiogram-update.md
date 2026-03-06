# aiogram-update: bump aiogram to latest stable

## Context

- The project currently allows `aiogram>=3.24.0`.
- The user asked to update aiogram to the newest release and refresh related dependency state.
- This repo uses `uv.lock`, so dependency changes must be reflected in both `pyproject.toml` and the lockfile.

## Scope

- In scope:
  - Raise the minimum supported `aiogram` version to the latest stable release.
  - Refresh `uv.lock` so resolved packages match the new constraint.
  - Fix direct code or test compatibility issues caused by the upgrade.
  - Run project verification focused on dependency upgrade impact.
- Out of scope:
  - Unrelated refactors.
  - Architectural rewrites.
  - Adding new libraries beyond what the resolver requires.

## Plan

1. [x] Update `pyproject.toml` to the new `aiogram` minimum version.
2. [x] Refresh `uv.lock` with upgraded dependency resolution.
3. [x] Run targeted tests and quality checks.
4. [x] Fix only upgrade-related compatibility issues.
5. [x] Re-run verification.
6. [x] Move plan to `completed/` if the change is done.

## Risks and Open Questions

- `aiogram-dialog` compatibility may constrain the maximum practical `aiogram` version.
- Lock refresh may upgrade transitive packages and expose unrelated flaky tests.
- `pyright` coverage in this repo is intentionally partial, so verification should follow existing project rails.

## Verification

- `uv lock`
- `uv run --group dev pytest tests/test_forwarded_message_formatting.py tests/test_e2e_flow.py tests/test_edit_message.py tests/test_e2e_startup.py tests/test_main_startup.py tests/test_webhook_updates.py tests/test_startup_error.py -q`
- `just check-changed`

## Definition of Done

- [x] `pyproject.toml` and `uv.lock` reflect the upgrade
- [x] Relevant tests pass
- [x] Changed Python files pass touched-file checks
- [x] Docs/plan moved to completed
