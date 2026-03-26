# Local Names (Локальные имена)

## Problem

Support agent names (`/myname`) are stored globally — one name per user across all bots. If an agent works in multiple bots, they share the same pseudonym everywhere. Bot owners cannot control agent naming within their bot.

The `use_local_names` toggle and `local_names: dict` field already exist in config, DB, and admin UI — but the actual logic is not implemented.

## Solution

When `use_local_names` is enabled for a bot, `/myname` saves the name into `bot_settings.local_names` (per-bot JSON dict) instead of the global `Users` table. Name lookups for replies also use the local dict.

## Behavior

### `/myname <name>`

| `use_local_names` | Action | Response |
|---|---|---|
| `True` | Save to `bot_settings.local_names[str(user_id)]` via `config.update_bot_setting()`. Check uniqueness among `local_names.values()` only. | `Имя сохранено как "X" (локально для этого бота)` |
| `False` | Current behavior — save to global `Users` table via `repo.save_user_name()`. Check uniqueness among global names. | `Имя сохранено как "X" (глобально)` |

### `/show_names`

| `use_local_names` | Output |
|---|---|
| `True` | `Локальные имена:\n` + names from `bot_settings.local_names.values()` |
| `False` | `Глобальные имена:\n` + names from `repo.get_all_users()` (current behavior) |

### Name resolution (replies to users)

When a support agent replies in the master chat, the bot resolves their display name:

| `use_local_names` | Lookup | On miss |
|---|---|---|
| `True` | `bot_settings.local_names.get(str(from_user.id))` | Error: prompt to use `/myname` + mention that local names mode is enabled |
| `False` | `repo.get_user_info(from_user.id)` (current behavior) | Error: prompt to use `/myname` + mention that global names mode is used |

Affected handlers:
- `cmd_resend` (line ~340) — reply from master chat to user
- `cmd_edit_msg` (line ~460) — edit forwarded reply
- `cmd_send` (line ~195) — mass send by IDs

### Helper function

Extract name resolution into a helper to avoid duplication across 3 handlers:

```python
def _resolve_agent_name(
    user_id: int,
    bot_settings: SupportBotSettings,
    user_info: Users | None,
) -> str | None:
    """Return agent display name or None if not set."""
    if bot_settings.use_local_names:
        return bot_settings.local_names.get(str(user_id))
    if user_info is not None:
        return user_info.user_name
    return None
```

## Data model

No changes. `local_names: dict` (JSON column) and `use_local_names: bool` already exist in:
- `SupportBotSettings` (Pydantic model)
- `BotSettings` (SQLAlchemy model)
- Admin UI toggle
- DB load/save logic

Storage format: `{"<user_id_str>": "<display_name>", ...}`

## Files changed

| File | Change |
|---|---|
| `bot/routers/supports.py` | Add `_resolve_agent_name()` helper; modify `cmd_myname`, `cmd_show_names`, `cmd_resend`, `cmd_edit_msg`, `cmd_send` |

## Edge cases

- Agent has global name but no local name, `use_local_names=True` → error, must set local name
- Owner toggles `use_local_names` off → bot falls back to global names, local dict preserved but unused
- `local_names` dict empty on first use → normal, agents register via `/myname`

## Testing

- `/myname` with `use_local_names=True` saves to local dict, not global
- `/myname` with `use_local_names=False` saves globally with "(глобально)" message
- `/show_names` shows correct label and source
- Reply without name set → error in both modes
- Duplicate name within local scope → rejected
- Duplicate name that exists globally but not locally → allowed when local mode
