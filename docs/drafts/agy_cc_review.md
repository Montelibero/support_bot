# Результаты код-ревью незакоммиченных изменений (Helper CC Assignee)

Проведено детальное ревью изменений в [`bot/customizations/helper.py`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py), [`bot/customizations/interface.py`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/interface.py), [`bot/routers/supports.py`](file:///home/itolstov/Projects/mtl/SupportBots/bot/routers/supports.py), [`tests/test_helper_cc.py`](file:///home/itolstov/Projects/mtl/SupportBots/tests/test_helper_cc.py) и плана [`docs/exec-plans/active/helper-cc-assignee.md`](file:///home/itolstov/Projects/mtl/SupportBots/docs/exec-plans/active/helper-cc-assignee.md).

---

## Находки по приоритетам

### P1 (Баги — обязательно чинить до коммита)

1. [`bot/customizations/helper.py#L664-L678`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py#L664-L678) — **`raise` в `callbacks_lang_get` при сбое отправки события оставляет бесконечный спиннер на кнопке в Telegram**
   - **Суть:** В блоке `try ... await bot.send_message(chat_id=HELPER_EVENTS_CHAT_ID, ...) except Exception:` выполняется откат записи в Redis, но в конце стоит безусловный `raise`.
   - **Почему:** Исключение пробрасывается наружу из aiogram-хендлера. Из-за этого `await callback.answer(...)` не вызывается. В интерфейсе Telegram у агента, нажавшего кнопку «Взять», она зависает в состоянии загрузки (анимация спиннера) на 30–60 секунд до таймаута клиента, без какого-либо алерта или сообщения об ошибке.
   - **Как чинить:** Вместо `raise` залогировать ошибку через `logger.exception(...)`, показать агенту алерт `await callback.answer("Не удалось закрепить задачу: ошибка отправки события", show_alert=True)` и сделать `return`.

2. [`bot/customizations/helper.py#L634-L656`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py#L634-L656) — **Состояние гонки (TOCTOU) при параллельном взятии одного тикета двумя агентами**
   - **Суть:** Проверка `existing = await _get_assignment(...)` и запись `await _set_assignment(...)` разнесены во времени через асинхронный вызов.
   - **Почему:** В комментарии указано: *«two agents clicking within the same moment must not both pass the check above (the Telegram send yields to the event loop for hundreds of ms)»*. Однако автор упустил, что `await _get_redis().get(...)` сам является точкой переключения контекста event loop! Если два агента кликают «Взять» с разницей в доли миллисекунды, оба корутина параллельно получают `None` из Redis, оба проходят проверку `existing is None`, затем оба делают `_set_assignment` (второй затирает первого), и оба отправляют дублирующие события `#skynet #helper command=taken` в служебный канал.
   - **Как чинить:** Использовать атомарный захват в Redis: `SET key payload NX=True` (когда тикет свободен). Если Redis вернул `None/False` — ключ уже занят конкурирующим запросом, сразу возвращать `await callback.answer("Тикет уже взят другим агентом!", show_alert=True)`. Для случая обновления тикета тем же агентом — выполнять сверку и перезапись атомарно (Lua-скрипт) либо с повторной проверкой.

3. [`bot/customizations/helper.py#L534-L557`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py#L534-L557) — **Гонка и случайное затирание нового тикета в `/ticket_close_{id}`**
   - **Суть:** Команда `cmd_ticket_close` ищет тикет через `_find_assignment_by_ticket`, а затем делает безусловный `await _get_redis().delete(key)`.
   - **Почему:** В `callbacks_lang_end` (L757–L763) автор обоснованно написал: *«Release the assignment only when it is still anchored to this ticket message: the user may already have a newer ticket taken by someone else...»*. Но в `cmd_ticket_close` этой проверки нет! Если агент закрывает старый тикет командой, а пользователь за время диалога уже прислал новый вопрос, который взял другой агент, `delete(key)` снесёт актуальное назначение пользователя в Redis. Кроме того, вызов `_get_redis().delete(key)` обходит существующую функцию `_delete_assignment`.
   - **Как чинить:** Удалять ключ только в том случае, если текущий `ticket_msg` в Redis в точности равен закрываемому `ticket_msg_id` (проверить перед удалением или в Lua-скрипте).

---

### P2 (Стоит поправить)

1. [`bot/customizations/helper.py#L256`, `L569`, `L594-L598`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py#L256) — **Скрытое состояние и риски при кэшировании в `self._assignment_cache`**
   - **Суть:** В инстансе `HelperCustomization` заведён словарь `self._assignment_cache`, куда `get_extra_text` кэширует запись, а `get_reply_markup` пытается её прочитать, чтобы сэкономить один `GET` в Redis.
   - **Почему:** Это преждевременная оптимизация ради экономии <0.1 мс. В `bot/customizations/loader.py:17` прямо указано, что кастомизации и их роутеры в идеале должны быть синглтонами/stateless. Если в будущем `get_customization(bot_id)` закешируют (или инстанс будет переиспользован в тестах/воркере), этот словарь превратится в утечку памяти и источник вечно «протухших» назначений (пользователь закрыл тикет, а в памяти висит старое значение).
   - **Как чинить:** Убрать `self._assignment_cache`. В `get_reply_markup` просто вызывать `await _get_assignment(...)`. Локальный сетевой оверхед одного GET ничтожен по сравнению с рисками рассинхрона состояния.

2. [`bot/customizations/helper.py#L24-L31`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py#L24-L31) — **Управление жизненным циклом синглтона `_redis_client`**
   - **Суть:** Глобальный `_redis_client: aioredis.Redis | None` создаётся лениво, но никогда явно не закрывается и жёстко привязывается к первому вызвавшему его event loop.
   - **Почему:** В асинхронном коде при смене или перезапуске event loop (характерно для тестов и воркеров) вызов методов на старом клиенте приведёт к `RuntimeError: Event loop is closed` / `Task attached to a different loop`. Кроме того, при shutdown приложения соединения не освобождаются (нет вызова `aclose()`).
   - **Как чинить:** Добавить функцию очистки/закрытия `close_redis()` для вызова в aiogram shutdown hook или сбрасывать `_redis_client = None`.

3. [`bot/customizations/helper.py#L485`, `L489`, `L520`, `L559`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py#L485) — **Отсутствие явного `parse_mode="HTML"` при отправке ответов в `cmd_tickets` и `cmd_ticket_close`**
   - **Суть:** В коде генерируются ссылки `<a href="...">` и экранируются спецсимволы юзернейма через `html.escape`, но в вызовах `message.answer(...)` параметр `parse_mode="HTML"` не указан.
   - **Почему:** Код всецело полагается на то, что у переданного инстанса бота в `default.parse_mode` выставлен `"HTML"`. Если бот сконфигурирован иначе или создан без дефолтного режима (например, в части тестов), агенты увидят сырые HTML-теги либо текст упадёт с ошибкой.
   - **Как чинить:** Явно указывать `parse_mode="HTML"` во всех `message.answer(...)`, содержащих HTML-разметку.

4. [`bot/customizations/helper.py#L607-L614`, `L712-L719`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py#L607-L614) — **Ручное извлечение `bot = callback.bot` и `assert bot is not None` вместо DI aiogram**
   - **Суть:** В хендлерах колбэков объект бота берётся из `callback.bot` с проверками на `None` и `assert`.
   - **Почему:** В aiogram 3 экземпляр `Bot` автоматически внедряется в хендлер через аргументы. Ручное извлечение и проверки — лишний бойлерплейт.
   - **Как чинить:** Добавить `bot: Bot` напрямую в сигнатуру методов `callbacks_lang_get` и `callbacks_lang_end`.

5. [`docs/exec-plans/active/helper-cc-assignee.md#L54`](file:///home/itolstov/Projects/mtl/SupportBots/docs/exec-plans/active/helper-cc-assignee.md#L54) — **Рассинхрон плана фичи с кодом и незакрытый DoD**
   - **Суть:** В плане зафиксировано: *«callbacks_lang_get (taken): SET the key after the event is sent»*, тогда как в реализации SET перенесён *до* отправки сообщения. Чекбоксы плана не отмечены, файл не перенесён в `completed/`.
   - **Как чинить:** Синхронизировать формулировку в плане с кодом и перенести план в `docs/exec-plans/completed/`.

---

### P3 (Вкусовщина и минорные замечания)

1. [`bot/customizations/helper.py#L467`, `L514`, `L520`, `L540`, `L546`, `L555`, `L561`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py#L467) — **Использование устаревшего `disable_web_page_preview=True`**
   - В Telegram Bot API 7.0+ и aiogram 3.x данный флаг устарел. Рекомендуется использовать `link_preview_options=types.LinkPreviewOptions(is_disabled=True)`.

2. [`bot/customizations/helper.py#L445-L447`, `L525-L529`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py#L445) — **Повторная компиляция и проверка одного и того же regex `ticket_close`**
   - Проверка команды `/ticket_close_(\d+)` выполняется дважды: сначала в `handle_master_message`, затем внутри `cmd_ticket_close`. Можно передавать уже сматченный `ticket_msg_id` или `Match`-объект.

3. [`bot/customizations/helper.py#L157`](file:///home/itolstov/Projects/mtl/SupportBots/bot/customizations/helper.py#L157) — **Упрощённый парсинг суффикса бота в `_foreign_command_mention`**
   - Конструкция `tokens[0].split("@", 1)[1]` не валидирует символы имени бота по правилам Telegram (`[a-zA-Z0-9_]`). Это безопасно благодаря внешнему `re.match`, но строгий regex был бы чище.

---

## Что упростить (Overengineering)

1. **Убрать сложный потоковый батчинг в `_find_assignment_by_ticket`**:
   Метод `_find_assignment_by_ticket` (L104–L130) содержит 26 строк с локальной асинхронной функцией `flush()`, списками `batch`, вызовами `batch.clear()` и `zip(..., strict=True)`. И всё это только ради того, чтобы найти один ключ по `ticket_msg`. У хелпер-бота общее количество одновременно открытых тикетов вряд ли превышает сотни. Достаточно переиспользовать общий генератор батчей из `_scan_assignments` или объединить логику обхода.
2. **Удалить кэш `self._assignment_cache`**:
   Полностью устранить словарь в `HelperCustomization.__init__`. Это сделает методы `get_extra_text` и `get_reply_markup` чистыми, исключит риски рассинхронизации данных и упростит юнит-тестирование.
3. **Упростить сигнатуры хендлеров**:
   Избавиться от `cast(Message, message)`, `assert bot is not None` и проверок `if bot is None:` за счёт стандартного DI aiogram (`bot: Bot`).

---

## Чего не хватает в тестах

1. **Тест отката (rollback) в `callbacks_lang_get` при падении `bot.send_message`**:
   Ветки строк L666–L676 (удаление назначения или восстановление `previous`, если была повторная привязка) сейчас не имеют ни одного теста.
2. **Тест отказа Redis при взятии тикета (`_set_assignment` raises `ConnectionError`)**:
   Проверить поведение бота, если Redis недоступен непосредственно в момент клика на кнопку «Взять» (деградация: алерт или продолжение без CC).
3. **Тест падения Redis на этапе сканирования в `cmd_ticket_close`**:
   Тест `test_ticket_close_delete_failure_degrades` проверяет ошибку на этапе `delete`, но падение на стадии `_find_assignment_by_ticket` (scan) не протестировано.
4. **Тест изоляции команд от обычных пользователей в ЛС**:
   Проверить, что если обычный пользователь отправляет `/tickets` или `/ticket_close_123` боту в личные сообщения, `handle_master_message` не перехватывает эти сообщения, и они штатно пересылаются в мастер-чат как текст тикета.
5. **Тест повреждённого ключа с нечисловым `user_id`**:
   Ветка `except ValueError: continue` (L478–L481) в `cmd_tickets` не покрыта тестом.
6. **Тест обработки поста с подписью к медиа (`F.caption.regexp`)**:
   Роутер регистрирует хендлер канала на `F.caption.regexp(r"^\s*#helper\b")`, но в `test_helper_cc.py` проверяются исключительно текстовые посты (`F.text`).

---

## Вердикт

**Чинить и коммитить** (P1-баги критичны: спиннер блокирует работу агента при сбоях сети, а гонки могут приводить к затиранию чужих тикетов).
