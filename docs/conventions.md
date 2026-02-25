# Conventions

## Change Scope

- Prefer small, focused diffs.
- Avoid unrelated refactors in feature/fix PRs.
- Do not silently change public contracts.

## Planning

- Any non-trivial change requires an execution plan in `docs/exec-plans/active/`.
- Use `docs/exec-plans/active/_template.md` as the baseline format.
- Move completed plans to `docs/exec-plans/completed/`.

## Testing

- For bug fixes: reproduce with a failing test first.
- Update/add tests for behavior changes.
- Keep tests deterministic and isolated when possible.

## Verification

Before merge, run project checks and include what was run in PR notes.

Touched-file rule:

- If a Python file is modified, it must pass new rails (`ruff` + `pyright`) in the same change.
- Use `just check-changed` during development to enforce this incrementally.

Recommended command set (to be standardized incrementally):

- `just test`
- `just lint`
- `just fmt`
- `just types`
- `just check-changed`
- `just check`

## Documentation

- Update docs when behavior/contracts/flows change.
- Keep terms aligned with `docs/glossary.md`.

## Commit Hygiene

- Commit messages should explain intent, not only file edits.
- Keep commits logically grouped and minimal.
