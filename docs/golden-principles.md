# Golden Principles

1. **No guessing**: unclear requirements must be clarified from docs/contracts or with a human.
2. **Behavior first**: preserve existing behavior unless change is explicitly requested.
3. **Small diffs**: incremental changes beat broad rewrites.
4. **Tests are a gate**: changes are verified by automated tests whenever feasible.
5. **Explicit boundaries**: prefer clear module contracts over implicit coupling.
6. **Parse, do not assume**: validate external payloads on boundaries.
7. **Observable failures**: errors should be logged with actionable context.
8. **Docs near code**: changed behavior must be reflected in repository docs.
9. **Deterministic workflow**: use repeatable commands and predictable outputs.
10. **Security by default**: never leak secrets or weaken protections for convenience.
