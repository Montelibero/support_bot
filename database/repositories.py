from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Messages, Users


class Repo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_message_ids(
        self, bot_id, user_id, message_id, resend_id, chat_from_id, chat_for_id
    ):
        self.session.add(
            Messages(
                bot_id=bot_id,
                user_id=user_id,
                message_id=message_id,
                resend_id=resend_id,
                chat_from_id=chat_from_id,
                chat_for_id=chat_for_id,
            )
        )
        await self.session.commit()

    async def get_message_resend_info(
        self,
        bot_id,
        message_id=None,
        resend_id=None,
        chat_from_id=None,
        chat_for_id=None,
    ) -> Messages | None:
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
        sl = select(Messages).filter(
            Messages.bot_id == bot_id, Messages.chat_for_id == user_id
        )
        result = await self.session.execute(sl)
        return result.scalars().first() is not None

    async def save_user_name(self, user_id, user_name, bot_id):
        result = await self.session.execute(
            select(Users).filter(Users.user_id == user_id)
        )
        user = result.scalars().first()
        if user:
            user.user_name = user_name
            user.bot_id = bot_id
        else:
            self.session.add(Users(bot_id=bot_id, user_id=user_id, user_name=user_name))
        await self.session.commit()

    async def get_user_info(self, user_id: int) -> Users | None:
        result = await self.session.execute(
            select(Users).filter(Users.user_id == user_id)
        )
        return result.scalars().first()

    async def get_all_users(self, with_username=False) -> list:
        result = []
        query_result = await self.session.execute(select(Users))
        for user in query_result.scalars():
            if with_username:
                result.append(f"{user.user_name} (#ID{user.user_id})")
            result.append(user.user_name)
        return result

    async def get_agent_message_counts(
        self, bot_id: int, master_chat_id: int
    ) -> list[tuple[int, int]]:
        """Return [(user_id, message_count)] for agent replies from master chat."""
        stmt = (
            select(
                Messages.user_id,
                func.count(Messages.record_id).label("message_count"),
            )
            .filter(
                Messages.bot_id == bot_id,
                Messages.chat_from_id == master_chat_id,
                Messages.user_id.isnot(None),
            )
            .group_by(Messages.user_id)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def get_total_user_messages(self, bot_id: int, master_chat_id: int) -> int:
        """Return total messages sent TO master chat (from users)."""
        stmt = select(func.count(Messages.record_id)).filter(
            Messages.bot_id == bot_id,
            Messages.chat_for_id == master_chat_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar() or 0
