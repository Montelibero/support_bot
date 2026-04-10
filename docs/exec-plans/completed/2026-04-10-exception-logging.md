# exception-logging: add contextual logging to all bot exception handlers

## Context

- Incident: при вызове `/alert_bad` у бота `clubobot` (chat `-1001563924467`) Telegram
  вернул `Bad Request: REACTION_INVALID` на `message.react([🙈])`. Исключение не
  было обработано и поднялось до `aiogram`, засоряя логи полным трейсбеком
  (`bot/routers/supports.py:508`, `cmd_alert_bad`).
- Эмодзи `🙈` входит в список стандартных реакций `ReactionTypeEmoji`
  (см. `.venv/.../aiogram/types/reaction_type_emoji.py`) — ошибка приходит из-за
  настроек конкретного чата (ограниченный allowed_reactions / отключены реакции),
  а не из-за самого значения.
- В других `except` блоках роутеров контекст беден: либо молча `pass`, либо
  `logger.error(ex)` без `bot_id` / `chat_id` / `message_id`, либо ответ пользователю
  без записи в лог. Это затрудняет диагностику в мультибот-окружении.
- Правила `AI_FIRST.md §4` (наблюдаемость) требуют структурированных сообщений,
  пригодных для парсинга агентом. Текущие логи этому не удовлетворяют.

## Scope

- In scope:
  - `bot/routers/supports.py`: защитить `cmd_alert_bad` от `TelegramBadRequest`
    (реакция невалидна для чата) и пройти по всем `except` блокам, добавив
    контекст (`bot_id`, `chat_id`, `message_id`) и запись в `logger`.
  - `bot/routers/admin_dialog.py`: подключить `loguru.logger` и залогировать
    все обработчики исключений (добавление/передача/активация/деактивация бота).
  - Верификация: `uv run python -m py_compile` на затронутых файлах.
- Out of scope:
  - Унификация формата логов под JSON — остаёмся на существующем `loguru`
    форматировании.
  - Рефакторинг архитектуры роутеров.
  - `bot/customizations/helper.py` — там уже есть `logger.exception`, не трогаем.
  - Валидационные `except ValueError` на пользовательском вводе
    (`cmd_ignore`, `mh_change_chat`, `mh_change_owner`) — это нормальный поток,
    не ошибка; оставляем без логов, только ответ пользователю.

## Plan

1. [x] Обернуть `message.react(...)` в `cmd_alert_bad` в `try/except
   TelegramBadRequest` с `logger.warning`, включающим `bot_id`, `chat_id`,
   `chat_title`, `message_id`.
2. [x] `cmd_send` (supports.py): заменить безконтекстный `logger.info(ex)` на
   `logger.warning` с `bot_id` и целевым `chat_id`.
3. [x] `cmd_edit_msg` (supports.py): логировать ошибки `edit_message_text`,
   кроме "message is not modified".
4. [x] `resend_message_plus` generic `except Exception` (supports.py): ошибка
   раньше проглатывалась без лога — добавить `logger.error` с
   `bot_id`/`src_chat_id`/`dst_chat_id`/`message_id`.
5. [x] Три обработчика реакций в `message_reaction` (supports.py): в ветке
   `else: logger.error(ex)` добавить контекст `bot_id`, `chat_id`, `message_id`
   для каждого из трёх блоков (admin без send_info, admin reply, user side).
6. [x] `admin_dialog.py`: добавить `from loguru import logger`.
7. [x] `admin_dialog.py`: залогировать исключения в
   `mh_change_token` (add bot), `mh_change_owner` (transfer), и в двух
   ветках `button_clicked` (activate/deactivate).
8. [x] Проверить синтаксис: `uv run python -m py_compile bot/routers/supports.py
   bot/routers/admin_dialog.py`.
9. [x] Перенести план в `completed/`.

## Risks and Open Questions

- Risk: увеличение шумности логов за счёт ранее тихих `pass`. Митигация:
  известные безобидные подстроки (`message is not modified`,
  `message to react not found`) по-прежнему игнорируются без лога.
- Risk: форматированные f-строки с `message.chat.title` могут содержать
  эмодзи/не-ASCII в логах. Приемлемо для `loguru`.
- Open question: стоит ли перевести логи на `logger.bind(bot_id=..., chat_id=...)`
  для структурированного вывода? Отложено — выходит за scope инцидента.

## Verification

- Command: `uv run python -m py_compile bot/routers/supports.py
  bot/routers/admin_dialog.py`
- Expected result: `OK`, без ошибок компиляции.
- Additional manual checks:
  - При следующей реакции на сообщение в чате с ограниченными реакциями лог
    должен содержать одну строку `WARNING Failed to set reaction — bot_id=...`
    вместо полного трейсбека.
  - `uv run --group dev pyright bot/routers/supports.py bot/routers/admin_dialog.py`
    — новых ошибок типов быть не должно (не проверялось в рамках задачи, т.к.
    изменения точечные и в рамках уже типизированных блоков).

## Definition of Done

- [x] Планированный scope доставлен
- [x] `py_compile` проходит
- [x] Docs/ADR обновлений не требуется (поведение не меняется,
  публичные контракты не затронуты)
- [x] В диффе только относящиеся к задаче изменения
