# helper-take-command: /take reply to claim a ticket without a button

## Context

- Current helper assignments are keyed per user:
  `helper:assign:{bot_id}:{user_id}`. Once an agent owns any ticket of a
  user, every new forwarded message from that user has no "Взять" button
  (`get_reply_markup` returns `None`) and another agent cannot claim it.
- Production case: a user already has an open, unclosed ticket on agent A and
  sends a second request that should be handled by agent B. The second request
  is effectively unstoppable: no button, no assignment, not listed in
  `/tickets`.
- User decisions:
  - support multiple simultaneous open tickets per user;
  - CC every active assignee (`CC @A @B`) so either can close faster;
  - no new **take** buttons in the thread — the new flow is a command;
  - after `/take`, install the existing **«Взял @agent»** close button on the
    claimed ticket, otherwise the agent has no one-click way to close it;
  - selected interaction: `/take` sent as a reply to the ticket message;
  - existing "Взять" button on a first, unassigned ticket remains (scope A).
    Removing all existing take buttons (scope B) is out of scope.

## Scope

- In scope:
  - Ticket-level assignments: one assignment per forwarded master message,
    not one per user.
  - `/take` master-chat command, used as a reply to a forwarded ticket
    message.
  - Multiple open tickets per user; each can have its own agent.
  - `CC @A @B` for all active assignees of the user.
  - `/tickets` lists every open ticket assignment, including multiple tickets
    of the same user.
  - Closing one ticket removes only its assignment; other tickets stay.
  - Lazy compatibility with existing per-user keys.
  - Command-taken tickets get the existing **«Взял @agent»** close button;
    `/ticket_close_<id>` remains the fallback.
  - Documentation and tests.
- Out of scope:
  - Removing the existing "Взять" button from first tickets (scope B).
  - `/take_<id>` by message id/link, `/assign @agent`, reactions, reply
    keywords, and an `/unassigned` queue.
  - Notifying agents other than the existing helper `taken` event.
  - Auto-taking a ticket when an agent replies.

## Design

### Storage

New key (ticket assignment):

```text
helper:assign:{bot_id}:{user_id}:{ticket_msg_id}
```

Value remains `{"agent": "...", "ticket_msg": ...}`.

Legacy key (read-only compatibility):

```text
helper:assign:{bot_id}:{user_id}
```

Rules:

- New claims use the ticket key with `SET NX`.
- `_get_ticket_assignment(bot, user, ticket)` checks the ticket key first,
  then the legacy key when its `ticket_msg` matches.
- `_get_assignments_for_user(bot, user)` returns all ticket keys plus a
  matching legacy assignment, deduplicated by `ticket_msg`.
- Closing/deleting removes the exact ticket key and, only when it is the same
  ticket, the legacy key.
- Existing keys are not migrated in bulk: no TTL, unknown production volume,
  and lazy compatibility keeps rollback simple.

### CC

`get_extra_text` collects all active assignments for the user, deduplicates
agents, and appends:

```text
CC @agent1 @agent2
```

Order: by ticket message id, oldest first. No cap for now.

### Buttons

- No assignment for the user: keep the existing "Взять" button.
- Any assignment exists: `get_reply_markup` keeps returning `None`; the new
  message has no take button.
- After a successful `/take`, replace the ticket keyboard with the same
  **«Взял @agent»** close button used by the old button flow
  (`EndCallbackData`). The button closes only that ticket.
- If the keyboard edit fails, the claim remains valid and the error is
  logged; `/ticket_close_<ticket_id>` is the fallback.
- The existing "Взял @agent" close button remains on tickets taken via the
  old button flow.

### `/take`

`handle_master_message` recognizes `/take` (and `/take@this_bot`) in the
master chat.

Validation order:

1. There is a `reply_to_message`, otherwise answer usage.
2. The replied message maps to a user ticket via
   `Repo.get_message_resend_info(bot_id, resend_id=ticket_msg_id,
   chat_for_id=master_chat)`; agent replies and arbitrary messages are
   rejected.
3. The sender has a Telegram `@username`; without it CC cannot work.
4. The ticket has a valid URL for the helper event, same rule as the button
   flow.
5. The ticket is not already assigned to another agent; a claim by the same
   agent is idempotent.

Claim flow:

- resolve `user_id` from the ticket mapping (`chat_from_id`);
- resolve client username via `bot.get_chat(user_id)` with `"-"` fallback;
- `SET NX` the ticket key before sending the helper event (same ordering as
  the button flow);
- send `command=taken` to the helper events channel and register the existing
  ACK timeout;
- on event-send failure, roll the claim back and answer with an error;
- on success, install the **«Взял @agent»** close button on the ticket and
  answer with the ticket id and `/ticket_close_<id>` as a fallback.

### Hook plumbing

`AbstractBotCustomization.handle_master_message` gains keyword-only
dependencies supplied by the core:

```python
async def handle_master_message(
    self,
    message: Message,
    bot_settings: SupportBotSettings,
    *,
    bot: Bot | None = None,
    repo: Repo | None = None,
) -> bool:
```

`cmd_resend` passes `bot` and `repo`. The default implementation
stays a no-op. Existing direct test calls with two positional arguments keep
working; `/take` requires the dependencies and answers with an internal error
if they are missing.

## Plan

1. [x] `bot/customizations/interface.py`
   - extend `handle_master_message` signature with optional `bot` and `repo`;
     keep default `False`.
2. [x] `bot/routers/supports.py`
   - pass `bot=bot, repo=repo` when calling the hook.
3. [x] `bot/customizations/helper.py`
   - split key helpers into ticket/legacy forms;
   - add `_get_ticket_assignment`, `_get_assignments_for_user`,
     `_claim_ticket`, `_delete_ticket_assignment`;
   - make `_scan_assignments` and `_find_assignment_by_ticket` parse both key
     shapes and expose `(user_id, ticket_msg)`;
   - update `callbacks_lang_get`, `callbacks_lang_end`, `/tickets`,
     `/ticket_close_<id>` to ticket-level semantics;
   - update `get_extra_text` to CC all active agents and
     `get_reply_markup` to keep suppressing new buttons while any assignment
     exists;
   - add `/take` parsing and `cmd_take`;
   - update `get_help_text` with `/take`.
4. [x] `tests/test_helper_cc.py` (or a focused new test module reusing its
       fixtures)
   - update existing take/close/CC tests for ticket-level keys;
   - `/take` reply claims a new ticket and leaves the old assignment;
   - `/take` installs the «Взял @agent» close button whose callback closes
     only that ticket;
   - `/take` without reply, on a non-ticket, without username, or on another
     agent's ticket is rejected;
   - `/take` by the same agent is idempotent;
   - helper event is sent, ACK pending is registered, send failure rolls back;
   - CC lists both agents, deduplicated;
   - `/tickets` lists two tickets of one user;
   - closing one leaves the other;
   - legacy per-user key is still listed, used for CC, and closable;
   - real dispatcher: `/take` survives the core catch-all.
5. [x] `README.md`, `docs/glossary.md`
   - document `/take`, multi-ticket assignments, and `CC @A @B`.
6. [x] Verification and plan move.

## Risks and Open Questions

- Risk: existing production keys are per-user. Mitigated by lazy dual-read;
  no bulk migration, rollback stays possible.
- Risk: two agents run `/take` simultaneously. `SET NX` on the ticket key
  makes one win; the loser gets an alert.
- Risk: helper event delivered but ACK lost. Existing ACK timeout behavior
  applies unchanged.
- Risk: stale "Взять" keyboard after `/take` if the edit fails. The claim
  remains valid; `/ticket_close_<id>` is the fallback, and the stale callback
  re-checks the ticket assignment and refuses stale claims.
- Risk: CC grows with many open tickets. Accepted for now; deduplicated and
  ordered.
- Decision: existing first-ticket buttons stay (scope A). If scope B is
  required later, close/take commands become the only interface.

## Verification

- Command: `uv run pytest -q`
- Expected: all existing and new helper tests pass. Actual: 250 passed.
- Command: `uv run ruff check ... && uv run ruff format --check ...`
- Expected: clean for touched files. Actual: clean.
- Command: `uv run pyright ...`
- Expected: no new errors relative to the current baseline. Actual: 0 errors.
- Manual checks:
  - first ticket: existing "Взять" button still works;
  - second request from an already assigned user: no button;
  - `/take` as a reply claims it for another agent and adds the
    «Взял @agent» close button;
  - `/tickets` shows both; forwarded messages show `CC @A @B`;
  - closing one ticket (button or `/ticket_close_<id>`) leaves the other and
    CC falls back to one agent;
  - a legacy assignment remains visible and closable.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass
- [x] Docs updated
- [x] No unrelated changes in diff
