"""Guards for the webhook allowed_updates subscription.

Regression: edited_message handlers live only on the support dispatcher,
but the webhook subscription was built from the admin dispatcher alone,
so Telegram never delivered edit updates and user-side edits silently died.
"""

from aiogram import Dispatcher
from aiogram_dialog import setup_dialogs

from bot.routers.admin import router as admin_router
from bot.routers.admin_dialog import dialog_all
from bot.routers.supports import router as support_router
from main import resolve_allowed_update_types


def _build_dispatchers() -> tuple[Dispatcher, Dispatcher]:
    main_dispatcher = Dispatcher()
    main_dispatcher.include_router(admin_router)
    main_dispatcher.include_router(dialog_all)
    setup_dialogs(main_dispatcher)

    support_dispatcher = Dispatcher()
    support_dispatcher.include_router(support_router)
    return main_dispatcher, support_dispatcher


def test_webhook_allowed_updates_cover_support_only_types():
    main_dispatcher, support_dispatcher = _build_dispatchers()

    allowed = resolve_allowed_update_types(main_dispatcher, support_dispatcher)

    for update_type in ("message", "edited_message", "callback_query"):
        assert update_type in allowed, (
            f"{update_type} missing from webhook subscription"
        )


def test_allowed_updates_is_union_of_all_dispatchers():
    main_dispatcher, support_dispatcher = _build_dispatchers()

    main_types = set(main_dispatcher.resolve_used_update_types())
    support_types = set(support_dispatcher.resolve_used_update_types())
    support_only = support_types - main_types
    assert support_only, "test premise broken: support dispatcher has unique types"

    allowed = set(resolve_allowed_update_types(main_dispatcher, support_dispatcher))

    assert support_only <= allowed
