# ADR (Architecture Decision Records)

Use ADRs to capture major technical decisions.

## When to Create an ADR

- Architectural boundary change
- New major dependency/service
- Public contract/protocol changes
- Significant trade-off with long-term impact

## Naming

`NNNN-short-kebab-title.md`

Example: `0001-helper-events-channel-protocol.md`

## Template

```markdown
# ADR NNNN: <Title>

## Status
Proposed | Accepted | Superseded

## Context
Why this decision is needed.

## Decision
What is chosen.

## Consequences
Positive, negative, and operational impact.

## Alternatives Considered
Other options and why they were not chosen.
```

## Lifecycle

- Do not rewrite old ADRs to fit new decisions.
- Mark old ADRs as `Superseded` and reference the replacing ADR.
