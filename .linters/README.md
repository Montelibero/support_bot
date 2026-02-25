# Linters and Structural Guardrails

This directory tracks structural checks that enforce architecture and process.

## Roadmap

1. **Phase 1 (non-blocking):**
   - import direction checks (warning mode)
   - cycle detection reports

2. **Phase 2 (blocking in CI):**
   - import direction checks fail build
   - cycle detection fail build

3. **Phase 3 (quality ratchet):**
   - module size thresholds
   - complexity thresholds
   - doc freshness checks

## Rule Quality Requirements

Each rule error should include:

- what was violated
- why it matters
- how to fix it
- link to relevant docs section
