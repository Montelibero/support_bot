# durable-telegram-delivery: Durable Telegram delivery queue

## Context

- Production updates can be acknowledged by the webhook before aiogram finishes
  processing them because request handlers currently use background execution.
- Transient transport failures against the configured local Telegram Bot API have
  caused `TelegramNetworkError` in `getMe` and reaction calls; the same failure can
  affect critical message delivery.
- `cmd_resend` performs `getMe` for logging on every message, adding an unrelated
  network dependency before delivery begins.

## Scope

- In scope:
  - Remove per-message `getMe` from the support message hot path.
  - Persist outbound delivery jobs in SQLite before acknowledging support-bot
    webhook updates.
  - Use a Redis Stream and FastStream consumer as the worker transport.
  - Retry transient Telegram transport errors after a configurable backoff.
  - Preserve current text, media, album, reply, and message-ID mapping behavior.
  - Make enqueue idempotent for repeated Telegram webhook updates.
  - Add recovery, observability, tests, and an operator runbook.
- Out of scope:
  - Exactly-once Telegram delivery; Bot API send methods have no client
    idempotency key, so an ambiguous timeout can still produce a duplicate.
  - Moving reactions into the durable delivery queue.
  - Temporal integration or a generic workflow engine.
  - New product limits or delivery allowlists.

## Expected Files

- Create `bot/delivery_queue.py`.
- Create `tests/test_delivery_queue.py`.
- Modify `main.py`.
- Modify `bot/routers/supports.py`.
- Modify `database/models.py`.
- Modify `database/repositories.py`.
- Modify `pyproject.toml` and `uv.lock`.
- Modify `tests/test_main_startup.py`.
- Modify `tests/test_webhook_updates.py`.
- Modify `tests/test_local_names.py`.
- Modify `single_bot.py` and `tests/test_single_bot_startup.py`.
- Create `docs/runbooks/durable-telegram-delivery.md`.
- Modify `docs/runbooks/README.md`.

## Plan

1. [x] Add failing model/repository tests for idempotent delivery-job creation,
       state transitions, due-job lookup, and successful-result persistence.
2. [x] Add the minimal SQLite delivery-job model and repository operations; rerun
       the focused tests to green.
3. [x] Add failing queue tests proving SQLite commit precedes Redis publication,
       duplicate enqueue returns the existing job, and publication failure leaves
       a recoverable pending job.
4. [x] Add FastStream Redis Stream transport with policy-driven acknowledgement,
       deletion of handled transport entries, delivery-ID messages, and a
       reconciler that republishes due SQLite jobs.
5. [x] Add failing worker tests for success, `TelegramNetworkError`, process
       interruption before acknowledgement, and retry without losing the job.
6. [x] Implement worker state transitions and transient/permanent error
       classification. Acknowledge a stream entry only after the resulting SQLite
       state is committed.
7. [x] Add failing handler tests showing `cmd_resend` performs no `getMe`, produces
       one idempotent delivery job, and preserves existing album preparation.
8. [x] Refactor `supports.py` into preparation/enqueue and execution phases while
       keeping the existing Telegram send implementation as the worker activity.
9. [x] Add failing startup tests asserting both support webhook handlers wait for
       enqueue completion (`handle_in_background=False`) and queue lifecycle hooks
       start and stop cleanly.
10. [x] Wire the queue, shared support-bot instances, Redis broker, worker, and
        reconciler in `main.py` using the existing `REDIS_URL`.
11. [x] Add FastStream Redis dependency and refresh the lockfile.
12. [x] Document queue states, Redis Stream inspection, pending-job recovery,
        transport outage handling, and the at-least-once duplicate caveat.
13. [x] Rebuild the belief map after structural changes and inspect affected
        boundaries.
14. [x] Run focused tests after every red/green cycle, then `just check-changed`,
        `just test`, and the relevant startup/webhook integration tests.

## Risks and Open Questions

- Telegram can accept a send request and lose the response; retrying then creates a
  possible duplicate. The chosen behavior prefers a duplicate over silent loss.
- Redis AOF is enabled but a host crash can still lose the newest writes depending
  on fsync policy. SQLite remains the source of truth and the reconciler repairs
  missing Redis notifications.
- Composite media delivery can succeed before a response timeout. Retrying the
  complete persisted album can therefore produce a duplicate, consistent with
  the at-least-once contract.
- FastStream Redis acknowledgement and pending-entry recovery behavior must be
  verified against the resolved library version, not assumed from defaults.
- The current SQLite database is single-node; worker concurrency must use short
  transactions and conditional claims to avoid double processing.

## Verification

- Command: `uv run --group dev pytest -q tests/test_delivery_queue.py`
- Expected result: all delivery queue and recovery scenarios pass.
- Command: `uv run --group dev pytest -q tests/test_local_names.py tests/test_main_startup.py tests/test_webhook_updates.py tests/test_reply_deleted.py`
- Expected result: existing support and webhook behavior remains green.
- Command: `just check-changed`
- Expected result: formatting, ruff, and pyright pass for every modified Python file.
- Command: `just test`
- Expected result: full suite passes with no failures.
- Additional manual checks:
  - Stop the local Bot API, enqueue a message, and verify the job remains retryable.
  - Restart the application and Bot API, then verify the same job is delivered and
    marked successful.
  - Inspect Redis Stream pending entries and SQLite state during the outage.

## Definition of Done

- [x] Critical support messages are persisted before webhook acknowledgement.
- [x] Transient Bot API transport failures do not silently lose persisted jobs.
- [x] Duplicate webhook delivery does not create duplicate pending jobs.
- [x] Worker restart recovers unfinished delivery jobs.
- [x] Existing message formats, media handling, and reply mappings are preserved.
- [x] Tests pass.
- [x] Runbook is updated.
- [x] No unrelated changes are present in the diff.
