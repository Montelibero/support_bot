# spam-block-words: block configured words before first support reply

## Context

- `block_links` already blocks links and media from private-chat users until
  support replies for the first time.
- Operators need bot-level stop words such as `USDT` to behave like links in
  that same pre-reply window.
- The agreed UX is simple replacement: the admin sends a full new word list;
  sending `-` clears the list.

## Scope

- In scope:
  - Add a per-bot stop-word list to bot settings.
  - Add an admin dialog screen to edit the list.
  - Show a clear hint that matching is case-insensitive substring matching.
  - Block matching messages only when `block_links` is enabled and support has
    not replied to that user yet.
  - Keep current link/media behavior unchanged.
  - Add tests before implementation.
- Out of scope:
  - Regex matching.
  - Whole-word-only matching.
  - Incremental add/remove commands.
  - Separate stop-word lists per topic/thread inside one support bot.

## Plan

1. [x] Add failing tests in `tests/test_spam_protection.py`:
   - `USDT` blocks a new user before support replies.
   - Matching is case-insensitive and by substring, so `TUSDT` blocks.
   - The same word is allowed after support replies.
   - The word is allowed when `block_links` is disabled.
2. [x] Add bot-setting storage:
   - `config/bot_config.py`: add `spam_block_words: list[str]`.
   - `database/models.py`: add JSON column with default empty list.
   - `single_bot.py`: seed the setting with an empty list.
3. [x] Add existing-DB compatibility:
   - Update DB bootstrap so existing `bot_settings` tables get the new column.
   - Make synchronous `load_from_db()` treat missing/NULL value as `[]`.
4. [x] Refactor spam detection in `bot/routers/supports.py`:
   - Keep media and entity checks.
   - Add case-insensitive substring matching against `message.text`.
   - Reuse the existing block reply text.
5. [x] Add admin UX in `bot/routers/admin_dialog.py`:
   - Add state/window/button `Изменить стоп-слова`.
   - Hint text:
     `Введите стоп-слова через запятую или с новой строки. Сообщение блокируется, если содержит стоп-слово как часть текста, без учета регистра. Пример: USDT заблокирует "USDT", "usdt", "TUSDT". Отправьте "-" чтобы очистить список.`
   - Parse comma/newline separated values, trim whitespace, drop empties.
   - `-` clears the list.
6. [x] Update README with the new setting behavior.
7. [x] Run targeted tests:
   - `uv run pytest tests/test_spam_protection.py -q`
8. [x] Run changed-file checks:
   - `just check-changed`
9. [x] Move this plan to `docs/exec-plans/completed/` after verification.

## Risks and Open Questions

- Risk 1: SQLAlchemy `create_all()` does not add columns to existing SQLite
  tables. Mitigation: add a guarded `ALTER TABLE` for the new JSON column.
- Risk 2: Substring matching is intentionally broad. `USDT` blocks `TUSDT`,
  `USDT123`, and `buy-usdt-now`; the admin hint says this explicitly.
- Question needing confirmation: none; behavior and clear marker are agreed.

## Verification

- Command: `uv run pytest tests/test_spam_protection.py -q`
- Expected result: spam-protection tests pass.
- Command: `uv run pytest tests/test_spam_protection.py tests/test_startup_error.py tests/test_single_bot_startup.py -q`
- Expected result: related tests pass.
- Command: `just check-changed`
- Expected result: changed Python files pass configured checks.
- Additional manual checks: inspect admin-dialog hint text for the `-` clear
  behavior and substring example.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass
- [x] Docs updated
- [x] No unrelated changes in diff
