# Reactions in chats with restricted allowed_reactions

## Symptoms

- Sentry / email alerts with
  `set_message_reaction ... Bad Request: REACTION_INVALID`.
- Bot does not set 👀 / 👍 / 🙈 in a particular chat while other chats
  are unaffected.
- User reactions in the support chat are not mirrored back to the user
  (specific emoji only, others work).

## Checks

1. Inspect recent log rows for a given bot and chat:
   ```
   bot_id=<id> chat_id=<id> ... set_message_reaction ... REACTION_INVALID
   ```
2. Confirm chat is a supergroup / restricted channel and not a private
   chat with the user (private chats rarely restrict reactions).
3. In Telegram → chat settings → **Reactions**, check whether the chat
   has `All reactions` or a restricted list.

## Likely causes

- Chat admin enabled "Some" reactions and excluded the emoji the bot
  wants to set (e.g. 👀, 🙈).
- Chat admin disabled reactions entirely.
- User proxied a custom emoji reaction (premium) which the destination
  chat does not explicitly allow.

## Recovery steps

1. Preferred: ask the chat admin to include the emoji the bot uses
   (at minimum `👀`, `👍` for Master Chat; `🙈` for user-side DMs).
2. If chat owner refuses / cannot change settings: accept the current
   behavior — the bot pre-checks `available_reactions` via `getChat`
   and silently skips disallowed emoji after the first lookup.
3. To force-refresh the cached list (e.g. after the admin updates the
   allowed reactions), restart the bot process. The in-memory cache
   has a 1h TTL and auto-invalidates on `REACTION_INVALID`.

## Verification

- Trigger a reaction scenario from the affected chat (admin reacts to
  a forwarded ticket) and confirm the expected emoji shows up on the
  user side (or is silently skipped without log spam).
- `grep REACTION_INVALID` in logs: after recovery, matches should stop
  for that `(bot_id, chat_id)` pair.

## Follow-up

- If the same chat appears repeatedly, consider whether the feature
  (e.g. `mark_bad` → 👀) is valuable in that chat; disable per-bot via
  admin dialog when not.
- Implementation reference: `bot/reactions.py` (TTL cache, pre-check,
  silent-skip); all call sites in `bot/routers/supports.py`.
