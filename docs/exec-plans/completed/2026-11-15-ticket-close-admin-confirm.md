# 2026-11-15: админ-права и подтверждение для `/ticket_close_<id>`

## Context

`/ticket_close_<id>` — аварийное закрытие тикета в мастер-чате. Сейчас команда
закрывает тикет сразу и без проверки прав: любой из участников мастер-чата,
случайно нажав на `/ticket_close_556` в тексте `/tickets`, закрывает тикет.
Уже был реальный мисклик.

Требуется: команда доступна только админам чата и выполняется только после
подтверждения.

## Scope

In scope:

- проверка прав: только админ чата (Telegram-статус), а не владелец бота;
- подтверждение закрытия инлайн-кнопкой, срок жизни 120 секунд;
- «Отмена» доступна любому админу чата;
- подтверждение закрытия — только тому, кто вызвал команду.

Out of scope:

- текст `/tickets` и `/take` не меняется;
- `/help` не меняется;
- кнопка `Закрыл @agent` (`callbacks_lang_end`) не меняется;
- работа команды вне мастер-чата (ЛС с ботом, канал событий) не добавляется.

## Design

Права: `bot.get_chat_member(chat_id, user_id)`, статусы сравниваются енумами
`ChatMemberStatus.CREATOR` / `ChatMemberStatus.ADMINISTRATOR`. Отказ при
отрицательном статусе, при `from_user is None` и при исключении от Telegram
(fail-closed). Проверка выполняется в `cmd_ticket_close` до чтения Redis и
повторно при нажатии «Закрыть» (админа могли разжаловать).

Подтверждение: команда резолвит тикет (как сейчас — чтобы на неизвестный или
уже закрытый id ответить сразу), затем отправляет в тот же тред сообщение
«Закрыть тикет 556 от #ID777 (взял @agent1)?» с кнопками «Закрыть тикет» и
«Отмена». Состояние в памяти не хранится: срок жизни берётся из `issued_at` в
callback data, после рестарта кнопки просто истекают.

Закрытие: логика из `cmd_ticket_close` вынесена в `_close_ticket(...)`
(удаление назначения, событие `command=closed`, pending ack, клавиатура
`Закрыл @x`) и вызывается из обработчика кнопки. Возвращает текст результата,
которым редактируется сообщение-подтверждение.

## Plan

1. [ ] `bot/customizations/helper.py`: `_is_chat_admin`, константа
   `CLOSE_CONFIRM_TTL_SECONDS = 120`, текст отказа.
2. [ ] Callback data: `CloseTicketCallbackData` (ticket, client, invoker,
   `issued_at`) и `CancelCloseCallbackData` (ticket); регистрация обработчиков в
   `_build_router`.
3. [ ] `cmd_ticket_close`: проверка прав → резолв тикета → подтверждение вместо
   закрытия.
4. [ ] `_close_ticket`, `_close_prompt`, `callbacks_ticket_close`,
   `callbacks_ticket_close_cancel`.
5. [ ] `tests/test_helper_cc.py`: существующие тесты `cmd_ticket_close`
   переводятся на схему «команда → подтверждение → закрытие»; добавляются
   кейсы из раздела Verification.
6. [ ] Обновить документацию, если поведение команд зафиксировано в docs.

## Risks and Open Questions

- Risk 1: аварийное закрытие стало двухшаговым — согласовано.
- Risk 2: +1 вызов `get_chat_member` на команду; при ошибке Telegram доступ
  запрещается (fail-closed), оператор повторит.
- Risk 3: «Отмена» доступна любому админу — сообщение-подтверждение может
  погасить не тот, кто вызвал; согласовано.
- Open question: нет.

## Verification

- Command: `uv run --group dev pytest tests/test_helper_cc.py -q`
- Expected: зелёные, включая новые кейсы:
  - не-админ: отказ, назначение в Redis цело, событие в канал не ушло;
  - админ: команда отправляет подтверждение, назначение пока цело;
  - «Закрыть тикет»: назначение удалено, событие `command=closed`, клавиатура
    `Закрыл @admin`, сообщение-подтверждение отредактировано;
  - «Отмена»: назначение цело, подтверждение отредактировано;
  - подтверждение нажимает другой человек: alert, изменений нет;
  - просроченный `issued_at`: alert, назначение цело;
  - повторное нажатие после закрытия: «уже закрыт», второго события нет;
  - `get_chat_member` бросил исключение: отказ, назначение цело.
- Command: `just check-changed`
- Expected: ruff + pyright чисто.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass
- [x] Docs updated
- [x] No unrelated changes in diff

## Outcome

- Права: `_is_chat_admin` (`bot.get_chat_member`, енумы
  `ChatMemberStatus.CREATOR` / `ADMINISTRATOR`), проверка при вызове команды до
  Redis и повторно при нажатии «Закрыть»; при исключении Telegram — отказ.
- Подтверждение: `CloseTicketCallbackData` (`ticket_msg_id`, `client_id`,
  `user_id`, `issued_at`) и `CancelCloseCallbackData`; TTL 120 секунд из
  `issued_at`, состояние в памяти не хранится. Жмёт только вызвавший, «Отмена»
  доступна любому админу чата.
- Закрытие вынесено в `_close_ticket(...)` (удаление назначения, событие
  `command=closed`, pending ack, клавиатура `Закрыл @x`); промпт после нажатия
  редактируется в результат через `_close_prompt`.
- `cmd_ticket_close` разбит на `_resolve_close_target` + показ подтверждения.
- README: `/ticket_close_<id>` описан как аварийное админское закрытие.

Проверка: `pytest` — 282 passed (7 новых кейсов в `tests/test_helper_cc.py`,
23 существующих переведены на схему «команда → подтверждение → закрытие»);
`ruff format --check`, `ruff check`, `pyright` по изменённым файлам — чисто.

## Code review (agy, 3 круга)

Отчёты: `docs/drafts/agy_ticket_close_admin_confirm_round{1,2,3}_review.md`,
последний — `docs/drafts/agy_ticket_close_admin_confirm_latest.md`.
Итог: круг 3 — P1/P2/P3 нет, вердикт «коммитить».

Исправлено по ревью:

- P1: проверка прав адресовалась чату сообщения-подтверждения, а не мастер-чату
  — админ любого другого чата, где стоит бот, мог поддельным callback закрыть
  тикет. Добавлен `_master_chat_settings` и guard «только в мастер-чате» в оба
  обработчика;
- P2: `issued_at` из будущего обходил TTL — проверяется `age < 0`;
- P2: `prompt.message_thread_id` мог отсутствовать у `InaccessibleMessage` —
  `getattr`;
- P3: явный `parse_mode="HTML"` в `_close_prompt`, `logger.error` вместо
  недостижимого `message.answer` при отсутствии бота, единый текст «Тикет N уже
  закрыт», убран лишний `if url`, истёкшее подтверждение сворачивается без
  мёртвых кнопок;
- тесты: диспетчеризация обоих callback через `Dispatcher`, `CREATOR`,
  разжалованный админ, сбой `get_chat_member`, сбой чтения Redis при
  подтверждении, будущий `issued_at`, откат назначения при сбое отправки
  события, отмена вне мастер-чата, команда без `from_user`.

Отклонено: `show_alert` в ветке «тикет уже закрыт» (там же редактируется
сообщение), упрощение guard `operator is None` (нужен для сужения типа).

Проверка после ревью: `pytest` — 294 passed; `ruff format --check`,
`ruff check`, `pyright` по изменённым файлам — чисто.
