# forwarded-message-ux: Mark forwarded user content in master chat

## Context

- Operators currently see forwarded user content as a normal incoming ticket body.
- This causes UX confusion because the bot does not distinguish user-authored text from forwarded third-party text.

## Scope

- In scope:
  - Detect forwarded Telegram messages in inbound private chat flow.
  - Add explicit forwarded marker with source type and source name/title when available.
  - Preserve the same behavior for replies, auto-replies, and resend mapping.
  - Cover forwarded behavior with automated tests.
- Out of scope:
  - New operator workflows or buttons.
  - Changes to user-facing auto-reply text.
  - Database schema changes.

## Plan

1. [x] Add failing tests for forwarded text formatting and edited forwarded messages.
2. [x] Run the targeted tests to verify they fail for the expected reason.
3. [x] Implement minimal forwarded-origin formatter and reuse it in inbound message and edit flows.
4. [x] Run targeted tests until green.
5. [x] Update docs if behavior contract changed.
6. [x] Run verification commands.

## Risks and Open Questions

- Risk 1: Telegram may provide forwarded metadata via both modern and legacy fields; normalization must support both.
- Risk 2: Edited forwarded messages must keep the forwarded marker and existing edited marker without changing reply mapping.
- Question needing confirmation: none; use explicit marker with type + name as agreed.

## Verification

- Command: `uv run pytest tests/test_forwarded_message_formatting.py -q`
- Expected result: all forwarded-message formatting tests pass.
- Additional manual checks: verify normal non-forwarded flow test still passes.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass
- [x] Docs updated
- [x] No unrelated changes in diff
