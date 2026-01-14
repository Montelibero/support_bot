import json
from datetime import datetime
from redis.asyncio import Redis

from config.bot_config import bot_config

redis = Redis.from_url(bot_config.REDIS_URL[:-1] + '7')


async def save_to_redis(chat_id, data: dict):
    data_name = f'{chat_id}:{round(datetime.now().timestamp())}'
    await redis.set(data_name, json.dumps(data))
