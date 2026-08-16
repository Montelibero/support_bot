# Durable Telegram Delivery

## Symptoms

- A support message is not forwarded after a temporary Bot API timeout.
- Logs contain `TelegramNetworkError`, `TelegramServerError`, or a retry notice
  with a `job_id`.
- Delivery resumes after the Bot API recovers, but the queue has accumulated work.

## Delivery Contract

- SQLite table `delivery_jobs` is the source of truth.
- Redis Stream `supportbots:telegram-delivery` wakes delivery workers.
- Jobs are idempotent by bot, source chat, source message, and delivery kind.
- Transient failures move a job to `retry`; permanent Telegram request errors move
  it to `failed`.
- Delivery is at least once. If Telegram accepts a send request but its response is
  lost, a retry can create a duplicate message.
- Album items are collected in one SQLite job. An item arriving after that job was
  already claimed is persisted as an independent continuation job instead of
  being discarded.
- A lease token prevents a stale worker from overwriting the state committed by a
  worker that reclaimed the same job.
- Redis Stream entries are deleted after their resulting SQLite state is committed;
  the Stream is transport, not delivery history.

## Checks

Inspect queue state in SQLite:

```sql
SELECT status, COUNT(*)
FROM delivery_jobs
GROUP BY status;

SELECT job_id, bot_id, status, attempt_count, next_attempt_at, last_error
FROM delivery_jobs
WHERE status IN ('pending', 'processing', 'retry', 'failed')
ORDER BY job_id;
```

Inspect the Redis Stream and consumer group:

```bash
redis-cli XINFO STREAM supportbots:telegram-delivery
redis-cli XINFO GROUPS supportbots:telegram-delivery
redis-cli XPENDING supportbots:telegram-delivery supportbots-delivery-workers
```

Check local Bot API connectivity using the procedure in
[switching-to-local-bot-api.md](switching-to-local-bot-api.md). Do not print bot
tokens in logs or incident notes.

## Recovery

1. Restore connectivity between the application and the configured Bot API.
2. Keep SQLite and the Redis volume intact. Do not manually delete pending entries
   during recovery.
3. Restart the application if its delivery worker is not running.
4. The SQLite reconciler republishes due jobs. A worker also reclaims Redis pending
   entries left by an interrupted worker.
5. Confirm that `pending`, stale `processing`, and `retry` counts decrease.
6. Review `failed` jobs separately. They represent permanent Telegram request
   errors and are not automatically retried.

Do not manually change a job to `succeeded` without confirming the destination
message exists and the corresponding `t_messages` mapping was saved.

## Verification

- A new support message creates a `delivery_jobs` row.
- Successful delivery changes its status to `succeeded`.
- `t_messages` contains the source and destination message mapping.
- Redis `XPENDING` does not continually grow during normal operation.
- Logs contain the same `job_id` for enqueue, retry, and completion events.

## Follow-up

- Investigate repeated transport timeouts even when retry eventually succeeds.
- Monitor the age of the oldest due job, not only the total queue length.
- Confirm the Redis AOF volume and SQLite data volume are included in backups.
