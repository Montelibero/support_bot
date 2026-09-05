# ignore-cjk-spam: Ignore CJK spam before support reply

## Context

- Support bots receive repeated unsolicited messages written with CJK ideographs.
- Stop-word lists cannot cover an entire writing system and currently trigger a rejection reply.

## Scope

- In scope: add a per-bot toggle, enabled by default, that silently ignores text containing CJK ideographs before the first support reply.
- In scope: persist the toggle, expose it in the admin dialog, test the behavior, and document it.
- Out of scope: language detection, permanent user bans, spam scoring, and changes after an operator has replied.

## Plan

1. [x] Add and persist the bot setting with a default value of enabled.
2. [x] Add the toggle to the bot settings dialog.
3. [x] Detect CJK ideographs and silently stop processing before forwarding or auto-replying.
4. [x] Add regression tests for default behavior, disabling, and post-reply behavior.
5. [x] Update user-facing documentation.
6. [x] Run focused tests and touched-file checks.

## Risks and Open Questions

- Legitimate Chinese, Japanese, or Korean messages may contain CJK ideographs; administrators can disable the filter per bot.
- Detection covers Unicode supplementary ideograph ranges without classifying all non-Latin scripts as spam.

## Verification

- `uv run pytest tests/test_spam_protection.py -q`: 8 passed.
- `just test`: 108 passed.
- `just check-changed`: formatting, Ruff, and Pyright passed.
- `git diff --check`: passed.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass
- [x] Docs updated
- [x] No unrelated changes in diff
