# exception-logging-audit: verify all except blocks have logging

## Context

- После трёх инцидентных правок
  (`2026-04-10-exception-logging`, `2026-04-10-badrequest-logging`, а также
  fix в коммите `993554c`) нужно убедиться, что во всём проекте не
  осталось production-обработчиков исключений без логирования.
- Аудит без изменений кода — результат фиксирует текущее состояние.

## Scope

- In scope: инвентаризация всех `except` в репозитории с классификацией
  каждого блока по статусу логирования.
- Out of scope: любые правки кода в рамках этого плана.

## Plan

1. [x] Найти все `except` в `**/*.py` через grep.
2. [x] Для каждого блока проверить наличие `logger.{warning,error,exception}`.
3. [x] Классифицировать блоки на: покрыт, плановая тишина, тестовый код.
4. [x] Зафиксировать результат в этом файле.

## Findings

**Покрыто логированием (✅):**

- `main.py:80,87` — startup webhook errors
- `config/bot_config.py:16,175,183,190,199,265,297` — 7 блоков, все DB/env
- `bot/customizations/helper.py:190` — `logger.exception`
- `bot/routers/admin_dialog.py:120,123,285,408,416,428` — add/transfer/
  activate/deactivate bot
- `bot/routers/supports.py:288,527,574,847,888,937,975,1024` — cmd_send,
  cmd_alert_bad, edit_message, resend_message_plus (обе ветки),
  3× message_reaction

**Плановая тишина (🟢), лог не нужен:**

- `bot/customizations/helper.py:188` — `except asyncio.CancelledError:
  return`. `CancelledError` — штатный сигнал отмены фоновой задачи,
  не является ошибкой.
- `bot/routers/admin_dialog.py:172,283` — `except ValueError`
  в валидации ID чата / owner ID. Пользовательский ввод, ответ идёт
  пользователю, это нормальный поток.
- `bot/routers/supports.py:228` — `except ValueError` в `/ignore <id>`
  при парсинге int. То же самое.

**Не-production код (⚪), вне scope:**

- `tests/conftest.py:187,201,239` — моки HTTP-сервера в тестах.

## Verification

- Command: `rg -n '^\s*except' -g '**/*.py'` + ручная сверка с таблицей
  выше.
- Expected: каждая строка из вывода попадает в одну из категорий выше.
- Additional: нового кода не добавлялось — отдельная проверка тестами
  не требуется, т.к. в предыдущем плане
  `2026-04-10-badrequest-logging` прогон `pytest -q` прошёл (55 passed).

## Definition of Done

- [x] Инвентаризация выполнена
- [x] Каждый `except` классифицирован
- [x] Нет незалогированных production-обработчиков
- [x] Результат зафиксирован в этом файле
