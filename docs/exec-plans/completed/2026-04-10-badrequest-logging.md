# badrequest-logging: log all TelegramBadRequest branches in resend_message_plus

## Context

- Инцидент: пользователь получает от бота ответ `Send error =(`
  (`bot/routers/supports.py:881`), но в логах нет ни строки об ошибке.
- Причина: в `resend_message_plus` ветка `except TelegramBadRequest`
  (`bot/routers/supports.py:847`) логирует только подслучай
  "message to reply not found" через `logger.warning`. Все остальные
  `TelegramBadRequest` доходят до user-facing ответа
  (`"Ошибка отправки ..."` в master-чате или `"Send error =("` в чате
  пользователя) **без записи в лог**.
- Это продолжение exec-plan
  `docs/exec-plans/completed/2026-04-10-exception-logging.md`, который
  по scope не тронул именно эту ветку, т.к. она уже содержала один
  `logger.warning` и выглядела "логируемой".
- Требование `AI_FIRST.md §4` — наблюдаемость: любой путь к ошибке должен
  оставлять машинно-парсируемый след с контекстом.

## Scope

- In scope:
  - `bot/routers/supports.py`, функция `resend_message_plus`, ветка
    `except TelegramBadRequest`: добавить `logger.error` для всех
    случаев, кроме успешного retry "reply not found".
  - Контекст лога: `bot_id`, `src_chat_id`, `dst_chat_id`, `message_id`,
    текст исключения.
- Out of scope:
  - Рефакторинг `resend_message_plus`.
  - Изменение пользовательских ответов (`"Send error =("`,
    `"Ошибка отправки ..."`) — UX не трогаем.
  - Дополнительная классификация `TelegramBadRequest` по подтипам.
  - Форматирование остального кода.

## Plan

1. [x] В `bot/routers/supports.py` в `except TelegramBadRequest` добавить
   `logger.error` с контекстом. Лог должен срабатывать:
   - если подстрока "reply/not found" не сматчилась;
   - ИЛИ если сматчилась, но `reply_to_message_id is None` (retry не
     производится, исключение прокидывается дальше).
2. [x] `uv run --group dev ruff format --check bot/routers/supports.py`.
3. [x] `uv run --group dev ruff check bot/routers/supports.py`.
4. [x] `uv run --group dev pyright bot/routers/supports.py`.
5. [x] `uv run --group dev pytest -q` — полный прогон.
6. [x] Перенести план в `docs/exec-plans/completed/`.

## Risks and Open Questions

- Risk: двойное логирование, если TelegramBadRequest с "reply not found"
  но без `reply_to_message_id` придёт в рекурсивный вызов. Митигация:
  рекурсивный retry уже делается с `reply_to_message_id=None`, там
  "reply not found" не возникнет повторно, и warning из первого вызова
  + error из второго вызова — корректная цепочка.
- Risk: шум в логах, если какой-то чат систематически возвращает
  `TelegramBadRequest`. Приемлемо — это и есть сигнал о проблеме.
- Open question: стоит ли поднять уровень "reply not found" с warning до
  info, раз это штатный retry? Отложено — вне scope.

## Verification

- Commands:
  - `uv run --group dev ruff format --check bot/routers/supports.py`
  - `uv run --group dev ruff check bot/routers/supports.py`
  - `uv run --group dev pyright bot/routers/supports.py`
  - `uv run --group dev pytest -q`
- Expected: все команды зелёные; тесты `55 passed`.
- Manual: при повторении инцидента "Send error =(" в логе появится
  строка `ERROR resend_message_plus TelegramBadRequest — bot_id=...`.

## Definition of Done

- [x] Код изменён только в scope
- [x] Все verification-команды зелёные
- [x] План перенесён в `completed/`
- [x] Коммит с `fix(logging): ...` сообщением
