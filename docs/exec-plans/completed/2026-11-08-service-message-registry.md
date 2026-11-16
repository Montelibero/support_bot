# 2026-11-08: Реестр сервисных сообщений бота (service-message registry)

## Context

Оператор отвечает в мастер-чате на техническое сообщение бота («Тикет 26905
закрыт (взял @decentral)», вывод `/tickets`, ошибки `/take`, автоответы) и
получает в ответ текст:

> Сообщение не отправлено. Не найдена история переписки в БД …
> пришлите /send ID123 …

Причина: `cmd_resend` (`bot/routers/supports.py`) считает любое сообщение,
отправленное ботом, «копией сообщения пользователя», а единственный
дискриминатор — строка в `t_messages` (`resend_id`). Для технических сообщений
её нет, поэтому код не отличает «бот написал сам» от «история переписки
потерялась» и печатает подсказку, которая в этом случае вредна. Та же проблема
в `cmd_edit_msg` («Не удалось отправить изменения =(»).

Подсказку убирать нельзя: кейс «ответил, думал что ушло, а запись в БД
потерялась» реальный и должен оставаться диагностируемым.

## Scope

- In scope:
  - реестр сообщений, которые бот отправил в мастер-чат **сам** (не копии
    сообщений пользователей);
  - тишина в `cmd_resend` и `cmd_edit_msg` при ответе на такое сообщение;
  - покрытие автоматическое для ядра и для плагинов (customizations).
- Out of scope:
  - изменение текста/поведения подсказки для реально потерянной истории;
  - реестр сообщений вне мастер-чата;
  - бэкфилл истории (невозможен без чтения переписки чата);
  - реакции/эмодзи вместо ответа.

## Design

Точка перехвата — middleware сессии aiogram (`bot.session.middleware`), потому
что через неё проходят все исходящие вызовы любого кода: `message.answer` в
helper-кастомизации, `bot.send_message` ядра, aiogram-dialog, будущие плагины.
`Bot` в проекте создаётся только через `make_bot()` (`config/bot_config.py`),
поэтому установка middleware там покрывает все режимы (multi/single/webhook).

- Хранилище: новая таблица `t_service_messages` (`bot_id`, `chat_id`,
  `message_id`, `record_add_date`), уникальный ключ по тройке. Новая таблица
  создаётся `Base.metadata.create_all()` — ALTER существующих таблиц не нужен.
- Запись: после успешного ответа Telegram (id берём из ответа: `Message`,
  `MessageId`, список `Message` для альбомов) и только если `chat_id` совпадает
  с `master_chat` этого бота.
- Доставки копий пользовательских сообщений не регистрируются: они уже в
  `t_messages`. Отличаем через `ContextVar`, выставляемый декоратором
  `@suppress_registration` на `resend_message_plus` и `execute_delivery_payload`.
- Решение в `cmd_resend`: есть строка в `t_messages` → доставляем; нет, но есть
  в реестре → тишина + `logger.info`; нет нигде → прежняя подсказка.
- Регистрация никогда не ломает отправку: любое исключение ловится и логируется.
- Ретеншн: при старте удаляем записи старше 90 дней.

## Plan

1. [ ] `database/models.py`: модель `t_service_messages`, `save_service_message()`,
   очистка старых записей в `update_db()`.
2. [ ] `database/repositories.py`: `Repo.get_service_message()`.
3. [ ] `bot/service_messages.py`: `ContextVar`, `suppress_registration`,
   `ServiceMessageRecorder`, `install_recorder`.
4. [ ] `config/bot_config.py`: установка рекордера в `make_bot()` (ленивый
   импорт, чтобы не было цикла config ↔ bot).
5. [ ] `bot/routers/supports.py`: декораторы на двух точках доставки, проверка
   реестра в `cmd_resend` и `cmd_edit_msg`.
6. [ ] `tests/conftest.py`: `MockRepo.get_service_message`.
7. [ ] `tests/test_service_messages.py`: unit-тесты рекордера + handler-тесты
   (тишина на сервисное, подсказка на неизвестное, доставка на копию).
8. [ ] `tests/test_bot_factory.py`: assert, что `make_bot()` ставит рекордер.
9. [ ] Docs: `docs/architecture.md`, `docs/glossary.md` (термин «Service
   Message»), переместить план в `completed/`.

## Risks and Open Questions

- Risk 1: дополнительный INSERT на каждое исходящее сообщение бота в мастер-чат
  (<1 мс в SQLite). Сбой БД не влияет на отправку — ошибка только логируется.
- Risk 2: сообщения, отправленные до деплоя, в реестре отсутствуют → для них
  подсказка ещё появится. Хвост уходит сам, бэкфилл не делаем.
- Risk 3: плагин, создавший `Bot` мимо `make_bot()`, не попадёт под рекордер.
  Сейчас таких нет; фиксируем тестом в `tests/test_bot_factory.py`.
- Question (решено): подсказку для реальной потери истории оставляем как есть.

## Verification

- Command: `uv run --group dev pytest tests/test_service_messages.py tests/test_handlers.py tests/test_edit_message.py tests/test_e2e_flow.py -q`
- Expected: все зелёные, включая новые кейсы.
- Command: `just check-changed`
- Expected: ruff + pyright без замечаний по изменённым файлам.
- Manual: в мастер-чате ответить на «Тикет N закрыт» → бот молчит; ответить на
  пересланное сообщение пользователя → ответ доходит.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass
- [x] Docs updated
- [x] No unrelated changes in diff

## Outcome

- Реализовано: `bot/service_messages.py` (рекордер + `suppress_registration`),
  `t_service_messages` + `save_service_message()` + ретеншн 90 дней,
  `Repo.get_service_message()`, тишина в `cmd_resend` и `cmd_edit_msg`,
  установка рекордера в `make_bot()`.
- Проверено: `pytest` — 263 passed (11 новых в `tests/test_service_messages.py`),
  `ruff format --check` и `ruff check` по изменённым файлам — чисто,
  `pyright` по изменённым файлам — 0 ошибок.
- Найдено попутно (вне скоупа, не правилось): `pyright` ругается на
  `Result.rowcount` в `DeliveryRepo.delete_succeeded`/`delete_all_succeeded`
  (`database/repositories.py`) — ошибка существовала до этой задачи.
- Открытый остаток: сообщения, отправленные до деплоя, в реестре отсутствуют;
  для них подсказка ещё появится, пока они не уйдут из истории чата.

## Code review (agy, 3 круга)

Отчёты: `docs/drafts/agy_service_messages_round{1,2,3}_review.md`,
последний — `docs/drafts/agy_service_messages_latest.md`.

Исправлено по ревью:

- P1: `EditMessage*` возвращают отредактированное сообщение → ранняя выходили в
  реестр как «новые» (через `/take` и `/ticket_close` в реестр попадали тикеты
  пользователей). Теперь edit-методы пропускаются (`_EDIT_METHODS` собирается
  из `aiogram.methods`).
- P1: проверка псевдонима оператора шла раньше фильтрации сервисных сообщений →
  оператор без имени получал ошибку вместо тишины. Порядок: `t_messages` →
  реестр (тишина) → резолв имени.
- P2: уведомления «Ошибка отправки» в мастер-чат отправлялись внутри
  `@suppress_registration` и не регистрировались → ответ на них давал
  ложную подсказку. Добавлен `registration_enabled()`.
- P3: удалён дублирующий индекс; `_extract_message_ids` понимает
  `MessageId`/`list[MessageId]` (`CopyMessage(s)`, `ForwardMessages`); закрыты
  сессии в тестах; добавлены тесты на SQLite (идемпотентность, retention),
  `is_master_chat`, сквозной путь middleware → БД, `cmd_edit_msg` и оператор
  без имени.

Отклонено (backlog/не расширяем scope): пакетная вставка альбомов, кэш
`master_chat` вместо pydantic, UTC в `record_add_date`, `int(master_chat)` guard,
id в логе `cmd_edit_msg`, интеграционный тест подавления через реальные
delivery-функции.

Остаток P3 после круга 3 (вердикт ревью: коммитить): тест `_EDIT_METHODS`
тавтологичен (можно заменить явным списком критичных методов); `Repo.get_service_message`
тянет ORM-объект вместо `exists()`; нет юнит-теста на строковый `chat_id` в
`_normalize_chat_id`.

Проверка после ревью: `pytest` — 275 passed; `ruff format --check`, `ruff check`,
`pyright` по изменённым файлам — чисто.
