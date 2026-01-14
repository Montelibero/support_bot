import asyncio
import os
from contextlib import suppress

import sentry_sdk
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisStorage
from aiogram_dialog import (
    setup_dialogs, )
from loguru import logger

from bot.routers.admin import router as admin_router
from bot.routers.admin_dialog import dialog_all
from bot.routers.supports import router as support_router
from config.bot_config import bot_config


async def aiogram_on_startup_polling(dispatcher: Dispatcher, bot: Bot) -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    # await setup_aiogram(dispatcher)
    # Устанавливаем команды для бота
    from config.bot_config import set_commands
    await set_commands(bot)
    with suppress(TelegramBadRequest):
        await bot.send_message(chat_id=bot_config.ADMIN_ID, text='Bot started')
    logger.info("Started polling")
    # await dispatcher.storage.redis.flushdb()


async def aiogram_on_shutdown_polling(dispatcher: Dispatcher, bot: Bot) -> None:
    logger.debug("Stopping polling")
    await bot.session.close()
    await dispatcher.storage.close()
    logger.info("Stopped polling")


async def aiogram_on_startup_webhook(dispatcher: Dispatcher, bot: Bot) -> None:
    # Определяем типы обновлений, которые используются в обработчиках
    allowed_updates = dispatcher.resolve_used_update_types()
    if 'message_reaction' not in allowed_updates:
        allowed_updates.append('message_reaction')
    logger.info(f"Used update types for webhook: {allowed_updates}")
    
    # Устанавливаем команды для бота
    from config.bot_config import set_commands
    await set_commands(bot)
    
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_webhook(
        url=f"{bot_config.BASE_URL}/{bot_config.SECRET_URL}/{bot_config.MAIN_BOT_PATH}",
        allowed_updates=allowed_updates
    )
    
    for bot_setting in bot_config.get_bot_settings():
        try:
            tmp_bot = Bot(token=bot_setting.token)

            if bot_setting.can_work:
                # Устанавливаем команды для дополнительного бота
                from config.bot_config import set_commands
                await set_commands(tmp_bot)
                
                await tmp_bot.set_webhook(
                    url=bot_config.other_bots_url.format(bot_token=bot_setting.token),
                    allowed_updates=allowed_updates
                )
            else:
                await tmp_bot.delete_webhook()
        except TelegramUnauthorizedError:
            logger.error(f'TelegramUnauthorizedError {bot_setting.username} {bot_setting.id} - disabling bot')
            bot_setting.can_work = False
            await bot_config.save_settings_to_db(bot_setting)
        except Exception as e:
            logger.error(f'set_webhook_error {bot_setting.username} {bot_setting.id}')
            logger.error(e)

    with suppress(TelegramBadRequest):
        await bot.send_message(chat_id=bot_config.ADMIN_ID, text='Bot started')
    logger.info("Started webhook")


async def close_connections(app):
    for obj in [app, *app.values()]:
        if isinstance(obj, Bot):
            await obj.session.close()
    await asyncio.sleep(0.250)


async def aiogram_on_shutdown_webhook(dispatcher: Dispatcher, bot: Bot) -> None:
    await bot.session.close()
    await dispatcher.storage.close()

@logger.catch
def main():
    logger.add("logs/SupportBot.log", rotation="1 MB", level='INFO')

    # Load bot settings from database (synchronously)
    bot_config.load_from_db()

    bot = Bot(token=bot_config.main_bot_token, default=DefaultBotProperties(parse_mode='HTML'))

    storage = RedisStorage.from_url(url=bot_config.REDIS_URL,
                                    key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True))

    main_dispatcher = Dispatcher(storage=storage)

    main_dispatcher.include_router(admin_router)
    main_dispatcher.include_router(dialog_all)
    setup_dialogs(main_dispatcher)

    from bot.middlewares.db import DbSessionMiddleware
    from bot.middlewares.config import ConfigMiddleware
    
    config_middleware = ConfigMiddleware(bot_config)
    
    # Register middleware on main dispatcher (admin bot)
    main_dispatcher.update.middleware(config_middleware)


    multibot_dispatcher = Dispatcher(storage=storage)
    multibot_dispatcher.update.middleware(DbSessionMiddleware())
    multibot_dispatcher.update.middleware(config_middleware)
    multibot_dispatcher.include_router(support_router)

    if os.environ.get('ENVIRONMENT') == 'production':
        from aiohttp import web
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, TokenBasedRequestHandler, setup_application
        main_dispatcher.startup.register(aiogram_on_startup_webhook)
        main_dispatcher.shutdown.register(aiogram_on_shutdown_webhook)

        app = web.Application()
        SimpleRequestHandler(dispatcher=main_dispatcher, bot=bot).register(app,
                                                                           path=f"/{bot_config.SECRET_URL}/{bot_config.MAIN_BOT_PATH}")
        bot_settings = {
            "default": DefaultBotProperties(parse_mode='HTML')
        }
        TokenBasedRequestHandler(
            dispatcher=multibot_dispatcher,
            bots={
                bot_setting.token: Bot(token=bot_setting.token, **bot_settings)
                for bot_setting in bot_config.get_bot_settings()
                if bot_setting.can_work
            },
            bot_settings=bot_settings
        ).register(app, path=f"/{bot_config.SECRET_URL}/{bot_config.OTHER_BOTS_PATH}")

        setup_application(app, main_dispatcher, bot=bot)
        setup_application(app, multibot_dispatcher)

        web.run_app(app, host=bot_config.WEB_SERVER_HOST, port=bot_config.WEB_SERVER_PORT)
    else:
        main_dispatcher.startup.register(aiogram_on_startup_polling)
        main_dispatcher.shutdown.register(aiogram_on_shutdown_polling)
        
        # Определяем типы обновлений, которые используются в обработчиках
        logger.info(f"Used update types for main_dispatcher: {main_dispatcher.resolve_used_update_types()}")
        
        # Запускаем поллинг с оптимизированными типами обновлений
        asyncio.run(main_dispatcher.start_polling(bot, allowed_updates=main_dispatcher.resolve_used_update_types()))


if __name__ == '__main__':
    if len(bot_config.SENTRY_DSN) > 10:
        sentry_sdk.init(
            dsn=bot_config.SENTRY_DSN,
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
        )
    main()
