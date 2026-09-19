# delivery-contract-retention: minimal delivery payload contract + delete-on-success

## Context

- Delivery queue (commit 870a2fd) serializes the entire incoming `types.Message`
  into `delivery_jobs.payload` and rebuilds it in the worker via
  `Message.model_validate`. This coupled the queue to aiogram internals and
  produced a prod incident: `Default` sentinels in `LinkPreviewOptions` broke
  `model_dump`, killing every agent reply and user forward in webhook mode
  (fixed in 1f8e2d8 with a serialization fallback — the fix works, the design
  remains fragile).
- `delivery_jobs` rows are never deleted: full texts of user/agent
  correspondence accumulate in SQLite indefinitely (`mark_succeeded` only
  flips status). Succeeded rows are read by nothing in the codebase.

## Scope

- In scope:
  - Explicit minimal payload contract (ids + file_ids + a few nested dicts)
    instead of whole-message serialization.
  - Worker operates on the contract; no `Message.model_validate` round-trip.
  - Backward compatibility: pending legacy payloads (key `message`) keep being
    deliverable after deploy; new payloads use key `content`.
  - Delete job rows immediately on successful delivery (guarded by lease
    token) instead of marking them `succeeded`.
- Out of scope:
  - Queueing the currently-direct send paths (cmd_send, cmd_edit_msg master
    and user branches) — behavior preserved as is.
  - Schema/migration changes (payload stays JSON).
  - Cleanup of `failed` jobs (rare, useful for debugging; revisit if needed).

## Plan

1. [ ] `bot/routers/supports.py`:
   - add `_delivery_content(message) -> dict`: message_id, chat_id,
     media_group_id, per-type file_ids (photo/document/sticker/audio/video/
     voice/video_note/animation), location/contact/venue dicts;
   - `enqueue_resend_message_plus` writes `payload["content"]` (no
     `_serialize_message_payload` dump of the whole message);
   - album path: `payload["messages"]` items become content dicts;
     continuation logic reads `content["message_id"]`;
   - refactor `resend_message_plus` to accept `(content: dict, ...)` instead
     of `message: types.Message`; direct callers (cmd_send, cmd_resend direct
     branches, cmd_edit_msg user branch, internal retry recursion) build
     content via the helper; error answers use `bot.send_message(chat_id=content["chat_id"])`;
   - remove `_serialize_message_payload` once unused.
2. [ ] `bot/delivery_queue.py`: `enqueue_album_item` continuation uses
   content key names.
3. [ ] `bot/routers/supports.py` `execute_delivery_payload`:
   - new payloads (`content` key): build media/text sends from the contract,
     no Message reconstruction;
   - legacy payloads (`message` key): keep current
     `Message.model_validate` path unchanged.
4. [ ] `database/repositories.py`: replace `mark_succeeded` with
   `delete_succeeded(job_id, lease_token) -> bool` — guarded
   `DELETE ... WHERE job_id = ? AND lease_token = ?`.
5. [ ] `bot/delivery_queue.py` `process`: on successful delivery delete the
   row, keep the success log line and `_forget_album_lock`.
6. [ ] Tests:
   - `_delivery_content` extraction for text/photo/media/location/contact/venue;
   - enqueue payload contains `content`, no `link_preview_options`/`from_user`
     anywhere in payload (regression for the Default incident);
   - worker delivers new-shape payload (mock bot asserts sends) and still
     delivers a legacy `message`-shaped payload;
   - successful `process` removes the job row; failed attempts keep it;
   - update existing `tests/test_enqueue_payload.py`,
     `tests/test_delivery_queue.py`.
7. [ ] Move plan to `completed/` when done.

## Risks and Open Questions

- Risk: pending legacy jobs at deploy time — mitigated by the `message`-key
  compat branch in the worker.
- Risk: original code uses independent `if` blocks per media type (not elif);
  a message with several media types sends several messages. Contract keeps
  independent file_id fields and independent sends to preserve semantics.
- Risk: `resend_message_plus` signature change touches six call sites —
  covered by existing + new tests.
- Accepted trade-off: a replayed webhook update after successful delivery now
  creates a new job (duplicate resend to master chat) instead of being
  swallowed by the existing `succeeded` row. Rare, cosmetic, no data loss.
- Album straggler arriving after its job was delivered now starts a fresh
  album job (7 s ready delay) and is delivered alone — verified acceptable
  path in `enqueue_album_item`.

## Verification

- Command: `uv run pytest -q`
- Expected result: all tests pass, including new contract/delete-on-success tests.
- Command: `uv run ruff check . && uv run ruff format --check .`
- Expected result: clean.
- Additional manual checks: after deploy — fresh user message → reply in
  master chat works; `delivery job queued` log shows compact payload; reply to
  a pre-deploy message still delivers (legacy branch); `delivery_jobs` has no
  succeeded rows after traffic.

## Definition of Done

- [x] Planned scope delivered
- [x] Tests pass
- [x] Docs updated
- [x] No unrelated changes in diff
