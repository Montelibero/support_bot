from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Messages, Users

class Repo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_message_ids(self, bot_id, user_id, message_id, resend_id, chat_from_id, chat_for_id):
        self.session.add(Messages(bot_id=bot_id, user_id=user_id, message_id=message_id, resend_id=resend_id,
                             chat_from_id=chat_from_id, chat_for_id=chat_for_id))
        await self.session.commit()

    async def get_message_resend_info(self, bot_id, message_id=None, resend_id=None, chat_from_id=None, chat_for_id=None) -> Messages:
        sl = select(Messages).filter(Messages.bot_id == bot_id)
        if message_id:
            sl = sl.filter(Messages.message_id == message_id)
        if resend_id:
            sl = sl.filter(Messages.resend_id == resend_id)
        if chat_from_id:
            sl = sl.filter(Messages.chat_from_id == chat_from_id)
        if chat_for_id:
            sl = sl.filter(Messages.chat_for_id == chat_for_id)
        result = await self.session.execute(sl)
        return result.scalars().first()

    async def has_user_received_reply(self, bot_id: int, user_id: int) -> bool:
        """Check if a user has received a reply from support."""
        sl = select(Messages).filter(Messages.bot_id == bot_id, Messages.chat_for_id == user_id)
        result = await self.session.execute(sl)
        return result.scalars().first() is not None

    async def save_user_name(self, user_id, user_name, bot_id):
        result = await self.session.execute(select(Users).filter(Users.user_id == user_id))
        user = result.scalars().first()
        if user:
            user.user_name = user_name
            user.bot_id = bot_id
        else:
            self.session.add(Users(bot_id=bot_id, user_id=user_id, user_name=user_name))
        await self.session.commit()

    async def get_user_info(self, user_id: int) -> Users:
        result = await self.session.execute(select(Users).filter(Users.user_id == user_id))
        return result.scalars().first()

    async def get_all_users(self, with_username=False) -> list:
        result = []
        query_result = await self.session.execute(select(Users))
        for user in query_result.scalars():
            if with_username:
                result.append(f'{user.user_name} (#ID{user.user_id})')
            result.append(user.user_name)
        return result

    async def get_stats(self, bot_id, master_chat_id) -> list:
        # Получаем статистику по пользователям
        stmt_users = (
            select(
                Users.user_name,
                func.count(Messages.user_id).label("message_count")
            )
            .join(Messages, Users.user_id == Messages.user_id)
            .filter(
                Messages.bot_id == bot_id,
                Messages.chat_from_id == master_chat_id
            )
            .group_by(Users.user_name)
        )
        users_result = await self.session.execute(stmt_users)
        user_stats = users_result.all()

        # Получаем общее количество сообщений
        stmt_total = (
            select(func.count(Messages.record_id))
            .filter(
                Messages.bot_id == bot_id,
                Messages.chat_for_id == master_chat_id
            )
        )
        total_result = await self.session.execute(stmt_total)
        total_messages = total_result.scalar()

        # Создаем список результатов
        result = []
        for user_name, message_count in user_stats:
            result.append(f"{user_name}: {message_count} messages")
        result.append(f"Total messages from users: {total_messages}")

        return result
