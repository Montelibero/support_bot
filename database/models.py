import datetime
import asyncio

from sqlalchemy import BigInteger, String, DateTime, select, func, event, Boolean, Integer, JSON
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config.bot_config import bot_config

# URL for async driver
DATABASE_URL = f"sqlite+aiosqlite:///{bot_config.SQLITE_FILE_NAME}"

engine = create_async_engine(DATABASE_URL, echo=False)

# Enable WAL mode for better concurrency
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

session_maker = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Users(Base):
    __tablename__ = "t_users"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_name: Mapped[str] = mapped_column(String(30))
    bot_id: Mapped[int] = mapped_column(BigInteger)
    user_add_date: Mapped[datetime.datetime] = mapped_column(DateTime(), default=datetime.datetime.now,
                                                             onupdate=datetime.datetime.now)


class Messages(Base):
    __tablename__ = "t_messages"
    record_id: Mapped[int] = mapped_column(primary_key=True)
    record_add_date: Mapped[datetime.datetime] = mapped_column(DateTime(), default=datetime.datetime.now)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    resend_id: Mapped[int] = mapped_column(BigInteger)
    chat_from_id: Mapped[int] = mapped_column(BigInteger)
    chat_for_id: Mapped[int] = mapped_column(BigInteger)


class BotSettings(Base):
    __tablename__ = "bot_settings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=True)
    token: Mapped[str] = mapped_column(String)
    start_message: Mapped[str] = mapped_column(String, default="")
    security_policy: Mapped[str] = mapped_column(String, default="default")
    master_chat: Mapped[int] = mapped_column(BigInteger, nullable=True)
    master_thread: Mapped[int] = mapped_column(BigInteger, nullable=True)
    no_start_message: Mapped[bool] = mapped_column(Boolean, default=False)
    special_commands: Mapped[int] = mapped_column(Integer, default=0)
    mark_bad: Mapped[bool] = mapped_column(Boolean, default=False)
    owner: Mapped[int] = mapped_column(BigInteger, nullable=True)
    can_work: Mapped[bool] = mapped_column(Boolean, default=False)
    ignore_commands: Mapped[bool] = mapped_column(Boolean, default=False)
    use_local_names: Mapped[bool] = mapped_column(Boolean, default=False)
    local_names: Mapped[dict] = mapped_column(JSON, default=dict)
    use_auto_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    block_links: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_reply: Mapped[str] = mapped_column(String, default="")
    ignore_users: Mapped[list] = mapped_column(JSON, default=list)


async def update_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def save_message_ids(bot_id, user_id, message_id, resend_id, chat_from_id, chat_for_id):
    async with session_maker() as session:
        session.add(Messages(bot_id=bot_id, user_id=user_id, message_id=message_id, resend_id=resend_id,
                             chat_from_id=chat_from_id, chat_for_id=chat_for_id))
        await session.commit()


async def get_message_resend_info(bot_id, message_id=None, resend_id=None, chat_from_id=None, chat_for_id=None) -> Messages:
    async with session_maker() as session:
        sl = select(Messages).filter(Messages.bot_id == bot_id)
        if message_id:
            sl = sl.filter(Messages.message_id == message_id)
        if resend_id:
            sl = sl.filter(Messages.resend_id == resend_id)
        if chat_from_id:
            sl = sl.filter(Messages.chat_from_id == chat_from_id)
        if chat_for_id:
            sl = sl.filter(Messages.chat_for_id == chat_for_id)
        result = await session.execute(sl)
        return result.scalars().first()


async def has_user_received_reply(bot_id: int, user_id: int) -> bool:
    """Check if a user has received a reply from support."""
    async with session_maker() as session:
        sl = select(Messages).filter(Messages.bot_id == bot_id, Messages.chat_for_id == user_id)
        result = await session.execute(sl)
        return result.scalars().first() is not None


async def save_user_name(user_id, user_name, bot_id):
    async with session_maker() as session:
        result = await session.execute(select(Users).filter(Users.user_id == user_id))
        user = result.scalars().first()
        if user:
            user.user_name = user_name
            user.bot_id = bot_id
        else:
            session.add(Users(bot_id=bot_id, user_id=user_id, user_name=user_name))
        await session.commit()


async def get_user_info(user_id: int) -> Users:
    async with session_maker() as session:
        result = await session.execute(select(Users).filter(Users.user_id == user_id))
        return result.scalars().first()


async def get_all_users(with_username=False) -> list:
    async with session_maker() as session:
        result = []
        query_result = await session.execute(select(Users))
        for user in query_result.scalars():
            if with_username:
                result.append(f'{user.user_name} (#ID{user.user_id})')
            result.append(user.user_name)
        return result


async def get_stats(bot_id, master_chat_id) -> list:
    async with session_maker() as session:
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
        users_result = await session.execute(stmt_users)
        user_stats = users_result.all()

        # Получаем общее количество сообщений
        stmt_total = (
            select(func.count(Messages.record_id))
            .filter(
                Messages.bot_id == bot_id,
                Messages.chat_for_id == master_chat_id
            )
        )
        total_result = await session.execute(stmt_total)
        total_messages = total_result.scalar()

        # Создаем список результатов
        result = []
        for user_name, message_count in user_stats:
            result.append(f"{user_name}: {message_count} messages")
        result.append(f"Total messages from users: {total_messages}")

        return result


if __name__ == "__main__":
    asyncio.run(update_db())
