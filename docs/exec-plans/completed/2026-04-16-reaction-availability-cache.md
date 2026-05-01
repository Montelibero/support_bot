# reaction-availability-cache: pre-check allowed reactions before set_message_reaction

## Context

- В мультибот-окружении часть чатов имеет ограниченный список
  `available_reactions` (например, супергруппа `-1001563924467` "Клуб: обратная
  связь" у бота `clubobot`). Любая попытка `set_message_reaction` с эмодзи вне
  белого списка возвращает `Bad Request: REACTION_INVALID`.
- Затронуты все четыре места, где бот выставляет реакцию:
  `bot/routers/supports.py:526` (🙈 при `mark_bad` в личке юзера),
  `bot/routers/supports.py:935` (👀 на "чужие" сообщения в master_chat),
  `bot/routers/supports.py:973` и `:1022` (👍-подтверждение),
  `bot/routers/supports.py:968` и `:1017` (проксирование любой реакции
  собеседника — включая ❤️, 🔥 и т.п.).
- Сейчас ошибки логируются как `logger.error`, из-за чего Sentry шлёт письма
  на каждое второе сообщение в таких чатах. Отключать реакции нельзя — фича
  нужна в других чатах.
- Связанные доки и инциденты:
  `docs/exec-plans/completed/2026-04-10-exception-logging.md` (первое
  столкновение, аналогичный `REACTION_INVALID` на 🙈), этот же чат;
  `docs/exec-plans/completed/2026-04-10-badrequest-logging.md`.
- Bot API возвращает список разрешённых реакций через `getChat`
  (`ChatFullInfo.available_reactions: list[ReactionType] | None`). `None`
  означает "разрешены все стандартные эмодзи-реакции"; список — whitelist;
  пустой список — реакции отключены.

## Scope

- In scope:
  - Новый модуль `bot/reactions.py` с TTL-кэшем allowed-реакций по
    `(bot_id, chat_id)` и хелпером `safe_set_message_reaction(...)`, который
    делает pre-check и молча пропускает реакцию, если она запрещена в чате.
  - Хелпер `safe_react_to_message(...)` для `message.react([...])`-варианта
    (используется в `cmd_alert_bad`).
  - Замена пяти прямых вызовов в `bot/routers/supports.py` на новые хелперы;
    удаление дублирующихся `try/except` c silent-skip (логика переезжает в
    хелпер).
  - Уровень логов для случая "реакция недоступна в чате" — `logger.warning`
    с контекстом (`bot_id`, `chat_id`, `chat_title`, `message_id`,
    `emoji`/`custom_emoji_id`). Для неожиданных ошибок `set_message_reaction`
    остаётся `logger.error` с полным контекстом.
  - Инвалидация кэша для конкретного `(bot_id, chat_id)` при получении
    `REACTION_INVALID` в обход pre-check (страхует на случай гонки/изменения
    настроек чата).
  - Юнит-тесты в `tests/test_reaction_availability_cache.py`.
- Out of scope:
  - Отказ от фичи 👀 (бот по-прежнему ставит "глазки" — просто не будет делать
    это в чатах, где 👀 не разрешён).
  - Унификация логирования за пределами реакций.
  - Кэш в Redis / между процессами — достаточно in-memory TTL-кэша.
  - Миграция в будущую `infrastructure`-папку (следуем текущей layout, см.
    `docs/architecture.md#current-state`).

## Design notes

### Публичный интерфейс `bot/reactions.py`

```python
async def safe_set_message_reaction(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    reaction: ReactionType,
    log_hint: str,      # напр. "admin reply", "user side", "admin no send_info"
) -> bool: ...

async def safe_react_to_message(
    message: Message,
    reaction: ReactionType,
    *,
    log_hint: str,
) -> bool: ...

async def invalidate_cache(bot_id: int, chat_id: int) -> None: ...
```

Возвращает `True`, если реакция успешно поставлена; `False` — если пропущено
(запрещено в чате или перехвачено известной ошибкой). Исключения наверх
не пробрасывает.

### Поведение

1. Берём allowed-список из кэша по `(bot_id, chat_id)`. Если нет —
   `bot.get_chat(chat_id)` и кладём в кэш с TTL = **1 час**.
2. Решение:
   - `available_reactions is None` → разрешены стандартные эмодзи. Если
     `reaction` — `ReactionTypeEmoji`, допускаем. Если
     `ReactionTypeCustomEmoji` — пропускаем (Telegram требует явный allow).
   - `available_reactions == []` → реакции отключены, пропускаем.
   - `available_reactions: list` → совпадение по `type` + `emoji`/
     `custom_emoji_id` (строгое равенство).
3. Если разрешено — вызываем `bot.set_message_reaction(...)`. Известные
   транзиентные ошибки (`message is not modified`, `message to react not
   found`) — silent-skip. `REACTION_INVALID` в этой ветке → инвалидируем
   кэш, пишем `logger.warning`, возвращаем `False` (не повторяем).
4. Если запрещено pre-check'ом — `logger.warning` один раз на сообщение,
   возвращаем `False`.
5. Если `get_chat` упал (сеть/флуд) — fail-open: пытаемся как раньше (с
   try/except), плюс `logger.warning` про кэш-мисс.

### Точки замены в `bot/routers/supports.py`

| Место | Текущий вызов | Заменить на |
|---|---|---|
| `:526` | `message.react([ReactionTypeEmoji(emoji="🙈")])` | `safe_react_to_message(message, ReactionTypeEmoji(emoji="🙈"), log_hint="alert_bad")` |
| `:932-936` | `bot.set_message_reaction(..., [👀])` + `except` `:938-951` | `safe_set_message_reaction(..., log_hint="admin no send_info")`; `except` удалить |
| `:965-969` | `bot.set_message_reaction(..., [message.new_reaction[0]])` | `safe_set_message_reaction(..., log_hint="admin proxy")` |
| `:970-974` | `bot.set_message_reaction(..., [👍])` | `safe_set_message_reaction(..., log_hint="admin ack")` |
| `:1014-1018` | `bot.set_message_reaction(..., [message.new_reaction[0]])` | `safe_set_message_reaction(..., log_hint="user proxy")` |
| `:1019-1023` | `bot.set_message_reaction(..., [👍])` | `safe_set_message_reaction(..., log_hint="user ack")` |

Дублирующиеся `except`-блоки (`:938-951`, `:975-988`, `:1024-1037`) удаляются
целиком — silent-skip и контекст логов переезжают в хелпер. `cmd_alert_bad`
`try/except` остаётся, но уже не нужен вокруг `message.react` (хелпер сам
обрабатывает).

## Plan

1. [x] Создать `bot/reactions.py` с TTL-кэшем, хелперами и sync-safe
       инвалидацией (dict + lock на запись `asyncio.Lock`, TTL через
       `time.monotonic()`).
2. [x] Заменить 6 вызовов в `bot/routers/supports.py` на новые хелперы;
       удалить дубли `try/except`; убедиться, что `TelegramBadRequest`-импорт
       остаётся только там, где реально нужен.
3. [x] Юнит-тесты `tests/test_reaction_availability_cache.py`:
   - [x] `available_reactions is None` → стандартный эмодзи разрешён, custom
         — нет;
   - [x] пустой whitelist → всё запрещено;
   - [x] non-пустой whitelist → только совпадения по `emoji`/
         `custom_emoji_id`;
   - [x] TTL: два подряд вызова делают один `get_chat` (счётчик на моке);
   - [x] инвалидация по истечении TTL и после `REACTION_INVALID`;
   - [x] `get_chat` кидает `TelegramBadRequest` → fail-open, попытка
         `set_message_reaction`, warning в логе, без re-raise;
   - [x] silent-skip на `message is not modified` и `message to react not
         found`.
4. [x] Интеграционный тест-регрессия: повторить сценарий из
       `tests/test_reaction_propagation.py` и `tests/test_reaction_matrix.py`
       (убедиться, что поведение зеркалирования не ломается при замене
       вызовов).
5. [x] Добавить краткий runbook `docs/runbooks/reactions-in-restricted-chats.md`
       (почему бот иногда не ставит реакцию, как диагностировать,
       как сбросить кэш через рестарт). Проиндексировать в
       `docs/runbooks/README.md`.
6. [x] Верификация: `just check-changed`, `just test`.

## Risks and Open Questions

- **Risk 1 — гонка между кэшем и изменением настроек чата.** Админ изменил
  `available_reactions` — кэш показывает устаревшее. Смягчение: TTL 1 ч +
  инвалидация при `REACTION_INVALID`.
- **Risk 2 — `get_chat` добавляет по одному вызову на первый реакшн в новом
  чате.** В мультибот-инсталляции это приемлемо; последующие вызовы идут из
  кэша. Альтернатива — прогрев на старте, но это усложняет init; не делаем.
- **Risk 3 — custom emoji reactions от premium-юзеров при проксировании.**
  Если чат-приёмник не белым списком разрешает этот `custom_emoji_id` —
  пропускаем с warning. Поведение строже, чем было: раньше Telegram
  возвращал ошибку и мы логировали error; теперь молчим.
- **Open Question 1.** TTL 1 час — ок, или лучше 15 мин? (По умолчанию
  принято 1 ч; если пользовательские правки allowed-реакций ожидаются
  чаще — понизим.)
- **Open Question 2.** Перезагружать кэш при `my_chat_member`
  (смена статуса бота в чате)? Сейчас — нет, только TTL/REACTION_INVALID.

## Verification

- Команда: `just check-changed` на изменённых файлах + `just test` на
  новых/затронутых тестах.
- Ожидаемый результат: все тесты зелёные; в e2e-логе эмуляция
  `REACTION_INVALID` превращается в один `warning` и не пробрасывает
  исключение.
- Дополнительные ручные проверки (staging бот `clubobot`):
  - В master_chat админ ставит 👍 на "чужое" сообщение — бот не пытается
    повесить 👀 (либо пытается один раз, получает `REACTION_INVALID`, после
    чего последующие пропуски идут без API-вызова).
  - Юзер в личке отправляет сообщение, помеченное `mark_bad` — 🙈 ставится,
    если в личке это разрешено (по умолчанию да).
  - В Sentry/почте нет новых сообщений с `REACTION_INVALID`.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass (`just check-changed`, `just test` — 71 passed)
- [x] Docs updated (`docs/runbooks/reactions-in-restricted-chats.md`, индекс)
- [x] No unrelated changes in diff
