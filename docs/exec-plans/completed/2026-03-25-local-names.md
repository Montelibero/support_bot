# Local Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement per-bot agent name resolution when `use_local_names` is enabled.

**Architecture:** All changes in `bot/routers/supports.py`. Add a helper `_resolve_agent_name()`, then modify 5 handlers to branch on `bot_settings.use_local_names`. No DB/config changes needed — infrastructure already exists.

**Tech Stack:** Python, aiogram, Pydantic (SupportBotSettings)

**Spec:** `docs/superpowers/specs/2026-03-25-local-names-design.md`

---

### Task 1: Add `_resolve_agent_name` helper + error message builder

**Files:**
- Modify: `bot/routers/supports.py` (add after `_build_master_chat_text` at ~line 87)
- Test: `tests/test_local_names.py` (create)

- [ ] **Step 1: Write tests for `_resolve_agent_name`**

Create `tests/test_local_names.py`:

```python
import pytest
from unittest.mock import MagicMock

from bot.routers.supports import _resolve_agent_name, _no_name_error_text


@pytest.fixture
def local_settings():
    s = MagicMock()
    s.use_local_names = True
    s.local_names = {"111": "Алексей"}
    return s


@pytest.fixture
def global_settings():
    s = MagicMock()
    s.use_local_names = False
    s.local_names = {}
    return s


def test_resolve_local_name_found(local_settings):
    assert _resolve_agent_name(111, local_settings, user_info=None) == "Алексей"


def test_resolve_local_name_missing(local_settings):
    assert _resolve_agent_name(999, local_settings, user_info=None) is None


def test_resolve_global_name_found(global_settings):
    user_info = MagicMock()
    user_info.user_name = "GlobalName"
    assert _resolve_agent_name(111, global_settings, user_info=user_info) == "GlobalName"


def test_resolve_global_name_missing(global_settings):
    assert _resolve_agent_name(111, global_settings, user_info=None) is None


def test_local_mode_ignores_global_user_info(local_settings):
    user_info = MagicMock()
    user_info.user_name = "GlobalName"
    # user_id 999 not in local_names → None even though user_info exists
    assert _resolve_agent_name(999, local_settings, user_info=user_info) is None


def test_error_text_local_mode():
    text = _no_name_error_text(use_local_names=True)
    assert "локальные имена" in text.lower()
    assert "/myname" in text


def test_error_text_global_mode():
    text = _no_name_error_text(use_local_names=False)
    assert "глобальные имена" in text.lower()
    assert "/myname" in text
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_local_names.py -v`
Expected: `ImportError` — `_resolve_agent_name` and `_no_name_error_text` don't exist yet.

- [ ] **Step 3: Implement helper functions**

In `bot/routers/supports.py`, add after `_build_master_chat_text` (after line ~87):

```python
def _resolve_agent_name(
    user_id: int,
    bot_settings: SupportBotSettings,
    user_info: object | None,
) -> str | None:
    """Return agent display name or None if not set."""
    if bot_settings.use_local_names:
        return bot_settings.local_names.get(str(user_id))
    if user_info is not None:
        return user_info.user_name  # type: ignore[union-attr]
    return None


def _no_name_error_text(use_local_names: bool) -> str:
    """Build error message when agent has no pseudonym."""
    mode = "включены локальные имена" if use_local_names else "используются глобальные имена"
    return (
        f'Сообщение не отправлено. Не найден ваш псевдоним ({mode}), '
        f'пришлите "/myname псевдоним" и повторите ваш ответ. '
        f"/show_names покажет занятые псевдонимы"
    )
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_local_names.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/routers/supports.py tests/test_local_names.py
git commit -m "feat(local-names): add _resolve_agent_name helper and error builder"
```

---

### Task 2: Modify `/myname` command

**Files:**
- Modify: `bot/routers/supports.py:109-129` (`cmd_myname`)
- Test: `tests/test_local_names.py` (append)

- [ ] **Step 1: Write tests for local `/myname`**

Append to `tests/test_local_names.py`:

```python
from bot.routers.supports import cmd_myname
from config.bot_config import BotConfig


@pytest.fixture
def bot():
    b = MagicMock()
    b.id = 12345
    return b


@pytest.fixture
def repo():
    from tests.conftest import MockRepo
    return MockRepo()


@pytest.fixture
def config():
    c = MagicMock(spec=BotConfig)
    c.update_bot_setting = MagicMock()  # AsyncMock set below
    from unittest.mock import AsyncMock
    c.update_bot_setting = AsyncMock()
    return c


def _make_message(text, chat_id, user_id=111):
    from unittest.mock import AsyncMock
    msg = AsyncMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    return msg


@pytest.mark.asyncio
async def test_myname_local_saves_to_settings(bot, repo, config):
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {}

    msg = _make_message("/myname Локальный", 222)
    await cmd_myname(msg, bot, repo, settings, config)

    assert settings.local_names[str(msg.from_user.id)] == "Локальный"
    config.update_bot_setting.assert_awaited_once_with(settings)
    assert "локально" in msg.answer.call_args[1]["text"].lower()


@pytest.mark.asyncio
async def test_myname_local_duplicate_rejected(bot, repo, config):
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {"999": "Занято"}

    msg = _make_message("/myname Занято", 222)
    await cmd_myname(msg, bot, repo, settings, config)

    assert str(msg.from_user.id) not in settings.local_names
    assert "занят" in msg.answer.call_args[1]["text"].lower()


@pytest.mark.asyncio
async def test_myname_global_shows_label(bot, repo, config):
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = False
    settings.local_names = {}

    msg = _make_message("/myname Глобал", 222)
    await cmd_myname(msg, bot, repo, settings, config)

    assert 111 in repo.users
    assert "глобально" in msg.answer.call_args[1]["text"].lower()


@pytest.mark.asyncio
async def test_myname_local_allows_globally_existing_name(bot, repo, config):
    """Name exists globally but not locally → allowed in local mode."""
    repo.users[999] = "Занято"  # global name exists
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {}

    msg = _make_message("/myname Занято", 222)
    await cmd_myname(msg, bot, repo, settings, config)

    assert settings.local_names[str(msg.from_user.id)] == "Занято"
    assert "локально" in msg.answer.call_args[1]["text"].lower()
```

- [ ] **Step 2: Run tests — verify new tests fail**

Run: `uv run pytest tests/test_local_names.py -v -k "myname"`
Expected: FAIL — `cmd_myname` doesn't accept `config` param yet and doesn't branch on `use_local_names`.

- [ ] **Step 3: Modify `cmd_myname` handler**

Replace the entire `cmd_myname` function in `bot/routers/supports.py` (lines 109-129):

```python
@router.message(Command(commands=["myname"]))
async def cmd_myname(
    message: types.Message,
    bot: Bot,
    repo: Repo,
    bot_settings: SupportBotSettings,
    config: BotConfig,
):
    if message.chat.id != bot_settings.master_chat:
        return
    if bot_settings.ignore_commands:
        return

    data = _require_text(message).strip().split(" ")
    user = _require_from_user(message)
    if len(data) < 2:
        await message.answer(text="Необходимо указать имя после команды")
        return

    username = " ".join(data[1:])

    if bot_settings.use_local_names:
        if username in bot_settings.local_names.values():
            await message.answer(text=f"Псевдоним {username} уже занят")
            return
        bot_settings.local_names[str(user.id)] = username
        await config.update_bot_setting(bot_settings)
        await message.answer(text=f'Имя сохранено как "{username}" (локально для этого бота)')
    else:
        if username in await repo.get_all_users():
            await message.answer(text=f"Псевдоним {username} уже занят")
            return
        await repo.save_user_name(
            user_id=user.id, user_name=username, bot_id=bot.id
        )
        await message.answer(text=f'Имя сохранено как "{username}" (глобально)')
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_local_names.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run existing `/myname` tests to check no regression**

Run: `uv run pytest tests/test_handlers.py -v`
Expected: existing tests may need `config` param added to `cmd_myname` call. If they fail, update `tests/test_handlers.py` to pass a mock `config` argument.

- [ ] **Step 6: Fix regressions in `test_handlers.py`**

Known breakages:
1. `cmd_myname` now requires `config` param — add it.
2. `test_cmd_myname_success` asserts `text='Имя сохранено как "Alex"'` but new code returns `'Имя сохранено как "Alex" (глобально)'` — update assertion.
3. MagicMock `settings.use_local_names` is truthy by default — explicitly set `settings.use_local_names = False` in both existing test fixtures.

Add `config` fixture:
```python
@pytest.fixture
def config():
    from unittest.mock import AsyncMock
    c = MagicMock()
    c.update_bot_setting = AsyncMock()
    return c
```

In both existing test functions, add to settings setup:
```python
settings.use_local_names = False
settings.local_names = {}
```

Update calls: `await cmd_myname(message, bot, repo, settings, config)`

Update assertion in `test_cmd_myname_success`:
```python
message.answer.assert_called_with(text='Имя сохранено как "Alex" (глобально)')
```

- [ ] **Step 7: Commit**

```bash
git add bot/routers/supports.py tests/test_local_names.py tests/test_handlers.py
git commit -m "feat(local-names): /myname branches on use_local_names"
```

---

### Task 3: Modify `/show_names` command

**Files:**
- Modify: `bot/routers/supports.py:132-140` (`cmd_show_names`)
- Test: `tests/test_local_names.py` (append)

- [ ] **Step 1: Write tests**

Append to `tests/test_local_names.py`:

```python
from bot.routers.supports import cmd_show_names


@pytest.mark.asyncio
async def test_show_names_local(bot, repo):
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {"111": "Алексей", "222": "Мария"}

    msg = _make_message("/show_names", 222)
    await cmd_show_names(msg, bot, repo, settings)

    text = msg.answer.call_args[1]["text"]
    assert "Локальные имена" in text
    assert "Алексей" in text
    assert "Мария" in text
    assert "#ID" in text


@pytest.mark.asyncio
async def test_show_names_global(bot, repo):
    repo.users = {111: "GlobalAlex"}
    settings = MagicMock()
    settings.master_chat = 222
    settings.ignore_commands = False
    settings.use_local_names = False

    msg = _make_message("/show_names", 222)
    await cmd_show_names(msg, bot, repo, settings)

    text = msg.answer.call_args[1]["text"]
    assert "Глобальные имена" in text
    assert "GlobalAlex" in text
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_local_names.py -v -k "show_names"`
Expected: FAIL — current `cmd_show_names` doesn't branch.

- [ ] **Step 3: Modify `cmd_show_names` handler**

Replace in `bot/routers/supports.py`:

```python
@router.message(Command(commands=["show_names"]))
async def cmd_show_names(
    message: types.Message, bot: Bot, repo: Repo, bot_settings: SupportBotSettings
):
    if message.chat.id != bot_settings.master_chat:
        return
    if bot_settings.ignore_commands:
        return

    if bot_settings.use_local_names:
        if bot_settings.local_names:
            names = " ".join(
                f"{name} (#ID{uid})" for uid, name in bot_settings.local_names.items()
            )
        else:
            names = "(пусто)"
        await message.answer(text=f"Локальные имена:\n{names}")
    else:
        all_users = await repo.get_all_users()
        label = "Глобальные имена:\n" if all_users else ""
        await message.answer(text=f"{label}{' '.join(all_users)}")
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_local_names.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/routers/supports.py tests/test_local_names.py
git commit -m "feat(local-names): /show_names shows local or global label"
```

---

### Task 4: Modify reply handlers (`cmd_resend`, `cmd_edit_msg`, `cmd_send`)

**Files:**
- Modify: `bot/routers/supports.py` — three handlers
- Test: `tests/test_local_names.py` (append)

This is the core change: replace inline `repo.get_user_info()` + error handling with `_resolve_agent_name()` + `_no_name_error_text()`.

- [ ] **Step 1: Write test for reply with local name**

Append to `tests/test_local_names.py`:

```python
from bot.routers.supports import cmd_resend


@pytest.mark.asyncio
async def test_resend_local_name_resolves(bot, repo, config):
    """When use_local_names=True and local name exists, reply is sent."""
    settings = MagicMock()
    settings.master_chat = -100
    settings.master_thread = None
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {"111": "Локал"}
    settings.mark_bad = False
    settings.use_auto_reply = False
    settings.block_links = False
    settings.ignore_users = []

    # Simulate: admin in master chat replies to a bot message
    reply_msg = MagicMock()
    reply_msg.from_user = MagicMock()
    reply_msg.from_user.id = bot.id  # bot's own message

    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = -100  # master chat
    msg.chat.type = "supergroup"
    msg.from_user = MagicMock()
    msg.from_user.id = 111  # admin user
    msg.reply_to_message = reply_msg
    msg.html_text = "Ваш ответ"
    msg.text = "Ваш ответ"
    msg.photo = None
    msg.document = None
    msg.sticker = None
    msg.audio = None
    msg.video = None
    msg.voice = None
    msg.video_note = None
    msg.animation = None
    msg.location = None
    msg.contact = None
    msg.venue = None
    msg.media_group_id = None

    # Seed message history so resend_info is found
    repo.messages.append({
        "bot_id": bot.id,
        "user_id": None,
        "message_id": 50,
        "resend_id": reply_msg.message_id,
        "chat_from_id": 999,  # original user chat
        "chat_for_id": -100,
    })

    await cmd_resend(msg, bot, repo, settings, config)

    # repo.get_user_info should NOT have been called (local mode)
    # The bot should have sent a message to the user (not an error reply)
    assert not msg.reply.called or "псевдоним" not in msg.reply.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_resend_global_name_missing_error(bot, repo, config):
    """When use_local_names=False and no global name → error mentioning global mode."""
    settings = MagicMock()
    settings.master_chat = -100
    settings.master_thread = None
    settings.ignore_commands = False
    settings.use_local_names = False
    settings.local_names = {}
    settings.mark_bad = False

    reply_msg = MagicMock()
    reply_msg.from_user = MagicMock()
    reply_msg.from_user.id = bot.id

    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = -100
    msg.from_user = MagicMock()
    msg.from_user.id = 111
    msg.reply_to_message = reply_msg

    repo.messages.append({
        "bot_id": bot.id,
        "user_id": None,
        "message_id": 50,
        "resend_id": reply_msg.message_id,
        "chat_from_id": 999,
        "chat_for_id": -100,
    })

    await cmd_resend(msg, bot, repo, settings, config)

    msg.reply.assert_called_once()
    error_text = msg.reply.call_args[0][0]
    assert "глобальные имена" in error_text.lower()
    assert "/myname" in error_text


@pytest.mark.asyncio
async def test_resend_local_name_missing_error(bot, repo, config):
    """When use_local_names=True but no local name → error mentioning local mode."""
    settings = MagicMock()
    settings.master_chat = -100
    settings.master_thread = None
    settings.ignore_commands = False
    settings.use_local_names = True
    settings.local_names = {}  # no names
    settings.mark_bad = False

    reply_msg = MagicMock()
    reply_msg.from_user = MagicMock()
    reply_msg.from_user.id = bot.id

    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = -100
    msg.from_user = MagicMock()
    msg.from_user.id = 111
    msg.reply_to_message = reply_msg

    repo.messages.append({
        "bot_id": bot.id,
        "user_id": None,
        "message_id": 50,
        "resend_id": reply_msg.message_id,
        "chat_from_id": 999,
        "chat_for_id": -100,
    })

    await cmd_resend(msg, bot, repo, settings, config)

    msg.reply.assert_called_once()
    error_text = msg.reply.call_args[0][0]
    assert "локальные имена" in error_text.lower()
    assert "/myname" in error_text
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_local_names.py -v -k "resend"`
Expected: FAIL — current code doesn't check `use_local_names`.

- [ ] **Step 3: Modify `cmd_resend` (master chat → user reply)**

In `bot/routers/supports.py`, replace the master-chat branch of `cmd_resend` (lines ~335-370). The key change: replace `repo.get_user_info()` + inline error with `_resolve_agent_name()` + `_no_name_error_text()`.

Current pattern (repeated in 3 handlers):
```python
user_info = await repo.get_user_info(from_user.id)
if user_info is None:
    await message.reply('Сообщение не отправлено. Не найден ваш псевдоним...')
    return
# ... use user_info.user_name and user_info.user_id
```

New pattern:
```python
from_user = _require_from_user(message)
user_info = None if bot_settings.use_local_names else await repo.get_user_info(from_user.id)
agent_name = _resolve_agent_name(from_user.id, bot_settings, user_info)
if agent_name is None:
    await message.reply(_no_name_error_text(bot_settings.use_local_names))
    return
support_user_id = from_user.id if bot_settings.use_local_names else user_info.user_id  # type: ignore[union-attr]
# ... use agent_name and support_user_id
```

Apply this pattern to `cmd_resend` master-chat branch (~lines 339-370):
- Replace `user_info = await repo.get_user_info(from_user.id)` with the new pattern
- Replace `user_info.user_name` with `agent_name`
- Replace `user_info.user_id` with `support_user_id`
- Replace hardcoded error string with `_no_name_error_text(bot_settings.use_local_names)`

- [ ] **Step 4: Modify `cmd_edit_msg` (master chat branch, ~lines 458-489)**

Same pattern substitution as step 3.

- [ ] **Step 5: Modify `cmd_send` (mass send, ~lines 193-235)**

Same pattern substitution as step 3.

- [ ] **Step 6: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add bot/routers/supports.py tests/test_local_names.py
git commit -m "feat(local-names): reply handlers use _resolve_agent_name"
```

---

### Task 5: Full verification

- [ ] **Step 1: Run `just check`**

Run: `just check`
Expected: fmt + lint + types + tests all pass.

- [ ] **Step 2: Fix any lint/type issues**

If `ruff` or `pyright` flag issues, fix them.

- [ ] **Step 3: Final commit if fixes were needed**

```bash
git add -u
git commit -m "fix(local-names): address lint/type issues"
```

- [ ] **Step 4: Move execution plan to completed**

```bash
mv docs/exec-plans/active/local-names.md docs/exec-plans/completed/local-names.md
```

(Create `docs/exec-plans/active/local-names.md` referencing this plan if not done.)

- [ ] **Step 5: Remove `# todo` comment**

In `config/bot_config.py:36`, remove `# todo` from `use_local_names: bool = False # todo`.
