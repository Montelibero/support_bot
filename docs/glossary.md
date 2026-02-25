# Glossary

| Term | Meaning |
| --- | --- |
| Support Bot | A Telegram bot that routes user messages to support operators. |
| Main Bot | Administrative bot that manages multiple support bots. |
| Multi Bot Mode | Runtime mode where many support bots are managed centrally. |
| Single Bot Mode | Runtime mode for one standalone support bot. |
| Bot Settings | Per-bot configuration (chat, policy, toggles, etc.). |
| Master Chat | Primary support chat where user messages are forwarded. |
| Master Thread | Optional forum topic/thread inside the master chat. |
| Operator | Human support agent replying to user tickets. |
| Ticket | Logical conversation unit with a user. |
| Callback | Telegram button interaction payload. |
| Helper Customization | Bot-specific behavior for helper workflow (`bot_id=5173438724`). |
| Helper Event | Structured one-line event sent to channel for helper workflow. |
| ACK | Acknowledgement that helper event was processed. |
| Duplicate ACK | ACK result meaning event was already processed idempotently. |
| Error ACK | Error response for malformed or failed helper event processing. |
| Idempotency Key | Stable key to deduplicate events, currently based on `url`. |
| Payload | Structured `key=value` data in channel event message. |
| Execution Plan | Task plan file in `docs/exec-plans/active/`. |
| ADR | Architecture Decision Record in `adr/`. |
| Guardrail | Automated rule (lint/test/check) enforcing architecture/process. |
