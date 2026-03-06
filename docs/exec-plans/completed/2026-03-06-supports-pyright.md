# supports-pyright: remove current pyright errors from supports router

## Context

- `uv run --group dev pyright bot/routers/supports.py` currently reports 34 errors.
- The errors are concentrated in `bot/routers/supports.py` and block touched-file type checks whenever this router changes.
- The goal is to make the file pass pyright without changing runtime behavior.

## Scope

- In scope:
  - Eliminate current pyright errors in `bot/routers/supports.py`.
  - Keep behavior unchanged except for type-safety guards where values may be absent.
  - Add or adjust tests only if a change in control flow needs explicit coverage.
- Out of scope:
  - Broad refactoring of router architecture.
  - New features.
  - Repo-wide pyright cleanup outside this file.

## Plan

1. [x] Group pyright failures by cause and inspect the affected code blocks.
2. [x] Add minimal guards/assertions/helpers for nullable Telegram fields and callback inputs.
3. [x] Fix signature/type mismatches in helper functions and media handling.
4. [x] Re-run targeted tests for touched behavior.
5. [x] Re-run `pyright` on `bot/routers/supports.py`.
6. [x] Move plan to `completed/` when the file is clean.

## Risks and Open Questions

- Telegram objects are heavily optional in aiogram types, so careless narrowing can change behavior if done with early returns in the wrong places.
- Some fixes may need `cast` or small helper functions rather than branching to avoid large diffs.
- Message callback types like `MaybeInaccessibleMessage` need careful handling to avoid new runtime assumptions.

## Verification

- `uv run --group dev pyright bot/routers/supports.py`
- `uv run --group dev pytest tests/test_forwarded_message_formatting.py tests/test_e2e_flow.py tests/test_edit_message.py tests/test_reply_deleted.py tests/test_reaction_matrix.py tests/test_reaction_propagation.py -q`
- `just check-changed`

## Definition of Done

- [x] `bot/routers/supports.py` passes pyright
- [x] Relevant tests pass
- [x] No unrelated behavior changes
- [x] Plan moved to completed
