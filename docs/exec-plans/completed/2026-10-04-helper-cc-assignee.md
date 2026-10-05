# helper-cc-assignee: CC the assignee on user follow-ups (helper bot)

## Context

- In the master chat, follow-up messages from a user drown: the agent who
  took their ticket does not see that the user wrote again.
- The helper customization already has a ticket lifecycle: "Взять" button →
  taken event → "Закрыл" button → closed event. But "who currently owns
  user X's ticket" is stored nowhere queryable: it lives only in the ticket
  message keyboard and in outbound helper-channel events, and
  `get_customization()` returns a fresh instance per message, so in-memory
  state is invisible to `get_extra_text`.
- Goal: once an agent takes a ticket, every further forwarded message from
  that user ends with `CC @username` until the ticket is closed; while an
  assignment is open, new tickets for that user carry no "Взять" button.
- User decisions:
  - trigger = the existing "Взять" button (not agent replies);
  - no TTL — support questions legitimately run for two weeks; the
    assignment lives until explicit close;
  - tag the real Telegram @username;
  - management surface: `/tickets` lists open assignments with message
    links; `/ticket_close_{message_id}` closes any ticket regardless of who took
    it — covers a deleted ticket message and a leaver who cannot press the
    button.

## Scope

- In scope:
  - Redis-backed assignment store (bot already runs Redis for FSM and the
    delivery queue; `REDIS_URL` exists).
  - CC line in `get_extra_text`, button suppression in `get_reply_markup`,
    SET/DEL in the existing callbacks.
  - Master-chat commands `/tickets` and `/ticket_close_{message_id}`
    dispatched through a new default-no-op contract hook
    `AbstractBotCustomization.handle_master_message` called by `cmd_resend`
    inside try/except (a crashing plugin falls back to core handling;
    bot-router ordering is intentionally untouched).
- Out of scope:
  - Router ordering changes (`main.py`, `single_bot.py`, `loader.py` are not
    touched; the plugin router keeps only callback/channel handlers).
  - CC on agent replies or message edits (only new user messages).
  - Changes to the taken/closed event flow and ACK mechanics.

## Plan

1. [ ] `bot/customizations/helper.py`:
   - module-level lazy `redis.asyncio.Redis` client from
     `bot_config.REDIS_URL` (shared by fresh instances and the singleton);
   - key `helper:assign:{bot_id}:{user_id}` → JSON value
     `{"agent": "username", "ticket_msg": <master chat message id>}` (raw
     username without `@`, rendered with the prefix at display time); agents
     without a Telegram @username are refused on take — CC could never
     notify them; no TTL;
   - `callbacks_lang_get` (taken): guard against an existing assignment by
     another agent (case-insensitive), then atomically claim via SET NX
     before the channel event is sent (re-anchoring by the same agent is a
     plain overwrite); a failed channel send rolls the claim back and
     alerts the agent instead of leaving the button spinner hanging;
   - `callbacks_lang_end` (successful close by the taker): `DEL` the key;
   - `get_extra_text`: safe `GET` (Redis errors → empty string, never
     raise); key present → append `\nCC @agent`;
   - `get_reply_markup`: key present → `None` (no "Взять" button), else the
     current button;
   - `/tickets` (master chat): `SCAN helper:assign:{bot_id}:*`, reply with
     one line per assignment — the ticket message id linked to
     `https://t.me/c/{internal_chat_id}/{ticket_msg}` (plain id when the
     chat is not a -100 supergroup), client `#ID`, assignee (HTML-escaped);
     empty state line when none; lines are chunked to stay under the
     Telegram 4096-char message cap; oldest tickets (smallest message id)
     first; each line ends with its `/ticket_close_{id}` command;
   - `/ticket_close_{message_id}` (master chat, e.g. `/ticket_close_556`):
     find the assignment whose `ticket_msg` matches, delete it, confirm;
     any agent may close; unknown id → polite reply; Redis failures during
     scan, read, and delete degrade to a polite error reply;
   - dispatch model: the commands are NOT router message handlers (the core
     catch-all `cmd_resend` shadows sub-router message handlers in aiogram).
     Instead the contract gains a default no-op hook
     `AbstractBotCustomization.handle_master_message(message, bot_settings)
     -> bool`; `cmd_resend` calls it for master-chat messages inside
     try/except — a True return consumes the message, a crash is logged and
     core handling continues (a broken plugin cannot break the bots);
     agents without a Telegram @username are refused on take; agents are
     tagged with their real `@username` (lstripped at render time).
2. [ ] Tests `tests/test_helper_cc.py` (new, mocked Redis):
   - taken click sets the key with the taker's username and ticket message id;
   - `get_extra_text` appends CC when the key exists, not when absent;
   - `get_reply_markup` suppresses the button while the key exists;
   - successful close deletes the key; failed close (not the taker) keeps it;
   - `/tickets` lists assignments with links and shows the empty state;
   - `/ticket_close_{id}` deletes the matching assignment (by anyone), unknown id
     answered;
   - `@bot_username` suffix is accepted for this bot and ignored for others;
   - commands are no-ops on a bot with a different id;
   - `#helper` channel posts from other bots or other chats are ignored;
   - Redis outage degrades: extra text without CC, no exception.
3. [ ] Move plan to `completed/` when done.

## Risks and Open Questions

- Risk: abandoned tickets keep the assignment forever (no TTL, by design);
  `/tickets` + `/ticket_close_{id}` are the escape hatch.
- Risk: Redis flush/persistence loss silently drops assignments — CC stops,
  the "Взять" button returns; degraded but consistent behavior.
- Risk: Redis unavailable at click time — taken event still goes out; the
  SET failure is logged, assignment just missing (same degradation path).
- Risk: customization routers receive updates from all bots — mitigated by
  the router-level `F.bot.id` filter (single line, pass-through is
  automatic); commands additionally ignore `@`-suffixes naming other bots.

## Verification

- Command: `uv run pytest -q`
- Expected result: all tests pass, including new helper CC tests.
- Command: `uv run ruff check . && uv run ruff format --check bot/customizations/helper.py tests/test_helper_cc.py`
- Expected result: clean.
- Additional manual checks: on the helper bot — take a ticket → user writes
  again → master chat message ends with `CC @username` and has no button;
  `/tickets` shows the open ticket with a working link; `/ticket_close_556` from
  another agent releases it → next message has no CC and the button is back.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass
- [x] Docs updated
- [x] No unrelated changes in diff
