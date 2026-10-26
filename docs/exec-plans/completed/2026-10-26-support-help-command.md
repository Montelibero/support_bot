# support-help-command: /help in attached chats with plugin-extensible sections

## Context

- Operators need a single place to discover the support-bot commands available
  in the chat where the bot works.
- Core commands are spread across `bot/routers/supports.py`; helper-specific
  commands (`/tickets`, `/ticket_close_<id>`) live in the plugin and are
  invisible to the core.
- Hard-coding the full list in the core would silently rot when a plugin adds
  commands. The plugin contract needs a help hook.

## Scope

- In scope:
  - `/help` handler in the support dispatcher (works for single-bot and
    multi-bot modes).
  - Availability gate: only the bot's attached/master chat
    (`message.chat.id == bot_settings.master_chat`), including topics.
  - Static core command list with short Russian descriptions and access notes.
  - New default no-op customization hook that lets plugins append their own
    help section.
  - Helper plugin overrides the hook with `/tickets` and
    `/ticket_close_<id>`.
  - Safe chunking so core + plugin text never exceeds Telegram's 4096-char
    message limit.
  - README documentation of the command and the plugin hook.
- Out of scope:
  - `/help` for the main admin bot (`main.py` admin dispatcher).
  - Changing Telegram's command menu (`set_commands` / `BotCommandScope`).
  - Auto-discovery of plugin commands: plugins must explicitly implement the
    help hook.
  - Per-user permission filtering of the listed commands; restrictions are
    documented in the help text instead.
  - Reworking existing command registration into a metadata registry.

## Proposed contract

Core behavior:

- `/help` in `bot_settings.master_chat`:
  - sends a header + core command list;
  - appends plugin-provided help, if any;
  - splits into several messages when the combined text is too long.
- `/help` anywhere else:
  - consumed silently (no reply, not forwarded as a user ticket).
- `ignore_commands` does not disable `/help`: operators must always be able to
  discover commands.

Customization hook (base implementation returns no extra text):

```python
async def get_help_text(
    self, bot_settings: SupportBotSettings
) -> str:
    """Return an HTML fragment appended to /help. Empty string = no section."""
    return ""
```

Plugin text is HTML (the global bot default already uses `parse_mode=HTML`),
so a plugin can add headings and links. A plugin is responsible for escaping
user-controlled values.

## Plan

1. [x] `bot/customizations/interface.py`
   - add default non-abstract `get_help_text(bot_settings) -> str` returning
     `""`; existing customizations keep working unchanged.
2. [x] `bot/customizations/helper.py`
   - override `get_help_text` with a "Helper" section:
     `/tickets` — open tickets and their assignees;
     `/ticket_close_<id>` — close a ticket by the ticket message id.
3. [x] `bot/routers/supports.py`
   - add `cmd_help` registered before the catch-all `cmd_resend`;
   - gate on `message.chat.id == bot_settings.master_chat`; other chats are
     consumed without an answer so `/help` is never forwarded as a ticket;
   - build core help from a module-level list of `(command, description)`
     entries; include only commands available in the master chat
     (`/start`, `/security_policy`, `/myname`, `/show_names`, `/ignore`,
     `/send`, `/stats`, `/link`, `/help`);
   - call `get_customization(bot.id).get_help_text(bot_settings)` inside
     `try/except`: a broken plugin is logged and core help is still sent;
   - chunk the final HTML on line boundaries at <= 4000 chars, preserving the
     header on the first message.
4. [x] `tests/test_help.py` (new)
   - default customization: `/help` in master chat lists core commands;
   - helper bot: response contains `/tickets` and `/ticket_close_`;
   - `/help` in a private user chat and a non-master group: no reply, and the
     message is not forwarded;
   - `/help` still answers when `ignore_commands=True`;
   - plugin hook raising an exception: core help is still delivered, error is
     logged;
   - long plugin text is split into messages each <= 4096 chars;
   - real dispatcher regression: `/help` in master chat is not swallowed by
     the catch-all handler.
5. [x] `README.md`
   - document `/help`, its master-chat-only availability, and the
     `get_help_text` plugin hook.
6. [x] Verification and plan move
   - run targeted and full tests, ruff, pyright on touched files;
   - move this plan to `docs/exec-plans/completed/`.

## Decisions

- "Чаты, куда прикреплён бот" = only the bot's `master_chat` (the chat linked
  via `/link` or configured in admin). Arbitrary groups where the bot is only
  a member do not get `/help`.
- `/help` in a private user chat is silently ignored: no reply, no forwarding
  to the master chat as a ticket.

## Risks and Open Questions

- Risk: plugins return malformed HTML → Telegram rejects the whole help
  message. Mitigation: plugin exceptions are caught, but malformed HTML is
  still a plugin bug; the hook contract explicitly says "valid HTML".
- Risk: plugin help text grows beyond one message → covered by chunking.
- Risk: a future core command is added but not listed in the static help list.
  Accepted for now; a metadata registry is a possible follow-up.

## Verification

- Command: `uv run pytest -q`
- Expected result: all tests pass, including the new `tests/test_help.py`.
  Actual: 170 passed.
- Command:
  `uv run ruff check bot/routers/supports.py bot/customizations tests/test_help.py && uv run ruff format --check ...`
- Expected result: clean for touched files. Actual: clean.
- Command: `uv run pyright bot/routers/supports.py bot/customizations/interface.py bot/customizations/helper.py tests/test_help.py`
- Expected result: no new errors relative to the current baseline.
  Actual: 0 errors (also fixed the pre-existing `nx` annotation error in
  `_claim_assignment`).
- Manual check: in a linked master chat, `/help` shows core and helper
  commands; in a user's private chat, `/help` produces no response; a long
  plugin help still arrives in multiple messages.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass
- [x] Docs updated
- [x] No unrelated changes in diff
