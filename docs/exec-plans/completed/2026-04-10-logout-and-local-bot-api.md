# logout-and-local-bot-api: owner-only /logout and switchable Bot API server

## Context

- Проект хочет уметь переключаться между Telegram cloud Bot API и собственным
  локальным Bot API сервером (`https://core.telegram.org/bots/api#using-a-local-bot-api-server`).
- Перед сменой сервера токен нужно разлогинить методом `logOut`
  (`https://core.telegram.org/bots/api#logout`); после этого на тот же
  API-сервер нельзя залогиниться обратно 10 минут.
- Служба работает в Docker в webhook-only режиме (`main.py:143`), любое
  изменение настроек требует ручного `docker-compose up -d --force-recreate`
  (justfile `rebuild`). Hot-swap `aiogram.Bot` сессий не требуется.
- Сейчас `Bot(token=...)` создаётся в 7 местах без `session`/`api` override —
  все идут в cloud. Единой фабрики нет.
- Полный план (с обоснованием и контекстом исследования) лежит в
  `~/.claude/plans/gleaming-watching-lake.md`.

## Scope

- In scope:
  - Фабрика `make_session` / `make_bot` в `config/bot_config.py`, читающая
    env `TELEGRAM_API_URL`. Если переменная пустая — cloud, иначе local
    `TelegramAPIServer.from_base(url, is_local=True)`.
  - Замена всех семи call-sites `Bot(token=...)` на `make_bot(...)`.
  - Новая команда `/logout` в `bot/routers/admin.py`, доступная только
    `bot_config.ADMIN_ID` в private чате с admin-ботом. Логаутит все
    support-боты из `bot_config.get_bot_settings()` и затем главный admin
    bot. Отчитывается батчами по 10 штук.
  - Unit-тест фабрики (новый `tests/test_bot_factory.py`).
  - Обновление `README.md` / `.env.example` (если есть) с описанием
    `TELEGRAM_API_URL` и workflow переключения.
- Out of scope:
  - Per-bot поле `api_server_url` в БД (потенциальный будущий шаг).
  - Подтверждение команды inline-кнопкой.
  - Автоматический рестарт контейнера после logout.
  - Изменения CI / docker-compose (запрещено стоп-листом).
  - Новые внешние зависимости — `AiohttpSession` и `TelegramAPIServer`
    уже идут из установленного `aiogram`, ADR не требуется.

## Plan

1. [x] Добавить фабрику `make_session` / `make_bot` и поле
   `TELEGRAM_API_URL` в `BotConfig` (`config/bot_config.py`).
2. [x] Заменить 8 прямых вызовов `Bot(token=...)` на `make_bot(...)`:
   `main.py` (3), `single_bot.py` (1), `bot/routers/admin_dialog.py` (4).
   Удалить неиспользуемые импорты `Bot` / `DefaultBotProperties`.
3. [x] Добавить команду `/logout` в `bot/routers/admin.py` (owner-only,
   private chat, батч по 10 строк отчёта). Логируется каждый бот с
   контекстом `bot_id`/`username`.
4. [x] Добавить `tests/test_bot_factory.py` — 3 теста
   (cloud без env, local с env, make_bot пропускает сессию).
5. [x] Обновить `tests/test_startup_error.py` — патч `main.make_bot`
   вместо `main.Bot` (3 patch-сайта).
6. [x] Обновить `README.md` разделом «Переключение между Telegram cloud
   и локальным Bot API сервером» и добавить `TELEGRAM_API_URL` в
   `.env.sample`.
7. [x] `just check` + полный `pytest` — 58 passed.
8. [x] Перенести план в `docs/exec-plans/completed/`.

## Risks and Open Questions

- Risk: `aiogram_on_startup_webhook` после logout без правки env попытается
  поставить webhook на разлогиненный токен и получит `Unauthorized`. Это
  ожидаемо — handler `main.py:80` уже логирует и выставляет `can_work=False`.
  Пользователь подтвердил: менять это поведение не нужно, `can_work` в
  logout-команде **не трогаем**.
- Risk: если в логике логаута что-то упадёт между support-ботами и главным
  admin-ботом, оператор получит частичный отчёт. Митигация — каждый бот в
  своём `try/except`, отчёты батчами по 10, финальный admin-бот отдельно.
- Risk: `bot.log_out()` требует закрытой HTTP-сессии на стороне клиента.
  Используем `async with make_bot(token) as temp_bot:` — контекст-менеджер
  закрывает сессию после выхода из блока; `log_out()` вызывается **внутри**
  блока, сессия закрывается сразу после. Если будет ошибка — проверить через
  явный `await temp_bot.session.close()` до `log_out()`.
- Open question: нужен ли `/logout` также в `single_bot.py`? Пока нет —
  single_bot mode используется для одиночного бота в поллинге, там нет
  admin-роутера. Решаем потом, если понадобится.

## Verification

- Command: `uv run --group dev ruff format bot config main.py single_bot.py
  tests && uv run --group dev ruff check bot config main.py single_bot.py
  tests && uv run --group dev pyright bot config main.py single_bot.py
  tests && uv run --group dev pytest -q`
- Expected result: форматирование без изменений, lint clean, pyright 0
  errors, все тесты зелёные (текущие 55 + новые 2 из
  `test_bot_factory.py`).
- Additional manual checks:
  - `grep -rn "Bot(token=" --include='*.py'` → только импорты (`from
    aiogram import Bot`) и использование внутри фабрики.
  - `just check` зелёный.
  - (опционально, в staging) выполнить `/logout` в личке admin-бота,
    убедиться в отчёте и логах.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass (`just check` + full `pytest` — 58 passed)
- [x] Docs updated (`README.md` + `.env.sample`)
- [x] No unrelated changes in diff
- [x] Plan moved to `docs/exec-plans/completed/`
