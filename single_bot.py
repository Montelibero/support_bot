#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
from contextlib import suppress

import sentry_sdk
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisStorage
from loguru import logger
from faststream.redis import RedisBroker

from bot.delivery_queue import DeliveryQueue
from bot.routers.supports import execute_delivery_payload, router as support_router
from config.bot_config import bot_config, make_bot
from database.models import session_maker
from database.repositories import Repo


async def aiogram_on_startup_polling(
    dispatcher: Dispatcher, bot: Bot, delivery_queue: DeliveryQueue
) -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    await delivery_queue.start()
    with suppress(TelegramBadRequest):
        await bot.send_message(chat_id=bot_config.ADMIN_ID, text="Single bot started")
    logger.info("Started polling")


async def aiogram_on_shutdown_polling(
    dispatcher: Dispatcher, bot: Bot, delivery_queue: DeliveryQueue
) -> None:
    logger.debug("Stopping polling")
    await delivery_queue.stop()
    await bot.session.close()
    await dispatcher.storage.close()
    logger.info("Stopped polling")


def main():
    # Настройка логирования
    logger.add("logs/SingleSupportBot.log", rotation="1 MB", level="INFO")

    from database.models import update_db

    asyncio.run(update_db())

    # Получаем токен бота поддержки из конфига
    support_token = bot_config.single_bot_token
    if not support_token:
        logger.error("Не найден токен бота поддержки (single_bot_token)")
        return

    # Создаем экземпляр бота
    bot = make_bot(support_token)

    # Получаем информацию о боте
    bot_info = asyncio.run(bot.get_me())

    # Создаем конфигурацию для одиночного бота
    single_bot_config = {
        str(bot_info.id): {
            "id": bot_info.id,
            "username": bot_info.username,
            "token": support_token,
            "start_message": bot_config.SINGLE_START_MESSAGE,
            "security_policy": bot_config.SINGLE_SECURITY_POLICY,
            "master_chat": bot_config.SINGLE_MASTER_CHAT or bot_config.ADMIN_ID,
            "master_thread": None,
            "no_start_message": False,
            "special_commands": 0,
            "mark_bad": False,
            "owner": bot_config.ADMIN_ID,
            "can_work": True,
            "ignore_commands": False,
            "use_local_names": False,
            "local_names": {},
            "use_auto_reply": bot_config.SINGLE_USE_AUTO_REPLY,
            "block_links": True,
            "spam_block_words": [],
            "auto_reply": bot_config.SINGLE_AUTO_REPLY,
            "ignore_users": [],
        }
    }

    # Устанавливаем конфигурацию в bot_config
    bot_config.json_config = single_bot_config

    # Настраиваем хранилище состояний
    storage = RedisStorage.from_url(
        url=bot_config.REDIS_URL,
        key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True),
    )

    # Создаем диспетчер
    dispatcher = Dispatcher(storage=storage)

    from bot.middlewares.db import DbSessionMiddleware

    dispatcher.update.middleware(DbSessionMiddleware())

    # Регистрируем маршрутизатор поддержки
    dispatcher.include_router(support_router)

    broker = RedisBroker(bot_config.REDIS_URL)
    delivery_queue = DeliveryQueue(session_maker, broker)

    async def deliver(payload: dict) -> list[int]:
        async with session_maker() as session:
            return await execute_delivery_payload(
                payload, bot, Repo(session), bot_config
            )

    delivery_queue.register_worker(deliver)

    # Регистрируем обработчики запуска и остановки
    dispatcher.startup.register(aiogram_on_startup_polling)
    dispatcher.shutdown.register(aiogram_on_shutdown_polling)

    # Запускаем бота на поллинге
    asyncio.run(dispatcher.start_polling(bot, delivery_queue=delivery_queue))


if __name__ == "__main__":
    # Инициализация Sentry
    if len(bot_config.SENTRY_DSN) > 10:
        sentry_sdk.init(
            dsn=bot_config.SENTRY_DSN,
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
        )
    main()
