# Architecture

## Current State

Current code is organized by technical areas:

- `bot/` — handlers, routers, middleware, bot-specific customization
- `config/` — runtime config and bot settings
- `database/` — persistence models and repositories
- `tests/` — automated tests

This structure is functional and should remain stable while migration happens in small slices.

## Target State

Target architecture follows dependency direction toward business logic:

`domain <- application <- infrastructure`
`                 <- interface`

- `domain`: entities, business rules, value objects
- `application`: use-cases and orchestration through ports
- `interface`: inbound adapters (Telegram handlers, CLI, API)
- `infrastructure`: outbound adapters (DB, Redis, external APIs)

## Migration Strategy

Use `expand -> migrate -> contract` for each feature/domain:

1. **Expand**: introduce new boundary/contracts without breaking old path
2. **Migrate**: move one behavior slice with tests
3. **Contract**: remove old path after parity is verified

Rules:

- No big-bang rewrites.
- Keep PRs small and reviewable.
- Preserve runtime behavior unless explicitly planned.
