from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import DeliveryJob, Messages, Users


class DeliveryRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def enqueue(
        self,
        *,
        bot_id: int,
        source_chat_id: int,
        source_message_id: int,
        delivery_kind: str,
        payload: dict,
    ) -> DeliveryJob:
        lookup = select(DeliveryJob).filter_by(
            bot_id=bot_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            delivery_kind=delivery_kind,
        )
        existing = await self.session.scalar(lookup)
        if existing is not None:
            return existing

        job = DeliveryJob(
            bot_id=bot_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            delivery_kind=delivery_kind,
            payload=payload,
        )
        self.session.add(job)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.session.scalar(lookup)
            if existing is None:
                raise
            return existing
        await self.session.refresh(job)
        return job

    async def get(self, job_id: int) -> DeliveryJob | None:
        return await self.session.get(DeliveryJob, job_id)

    async def claim(self, job_id: int, *, now: datetime) -> DeliveryJob | None:
        lease_token = uuid4().hex
        statement = (
            update(DeliveryJob)
            .where(
                DeliveryJob.job_id == job_id,
                or_(
                    and_(
                        DeliveryJob.status.in_(("pending", "retry")),
                        or_(
                            DeliveryJob.next_attempt_at.is_(None),
                            DeliveryJob.next_attempt_at <= now,
                        ),
                    ),
                    and_(
                        DeliveryJob.status == "processing",
                        DeliveryJob.updated_at <= now - timedelta(seconds=60),
                    ),
                ),
            )
            .values(
                status="processing",
                attempt_count=DeliveryJob.attempt_count + 1,
                lease_token=lease_token,
                updated_at=now,
            )
            .returning(DeliveryJob)
        )
        result = await self.session.execute(statement)
        job = result.scalar_one_or_none()
        await self.session.commit()
        return job

    async def enqueue_album_item(
        self,
        *,
        bot_id: int,
        source_chat_id: int,
        source_message_id: int,
        delivery_kind: str,
        payload: dict,
        ready_at: datetime,
    ) -> DeliveryJob:
        job = await self.enqueue(
            bot_id=bot_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            delivery_kind=delivery_kind,
            payload=payload,
        )
        incoming = payload["messages"][0]
        messages = list(job.payload.get("messages", []))
        if not any(item["message_id"] == incoming["message_id"] for item in messages):
            messages.append(incoming)
        merged_payload = {**job.payload, "messages": messages}
        if job.status in ("pending", "retry"):
            await self.session.execute(
                update(DeliveryJob)
                .where(
                    DeliveryJob.job_id == job.job_id,
                    DeliveryJob.status.in_(("pending", "retry")),
                )
                .values(
                    payload=merged_payload,
                    next_attempt_at=ready_at,
                    last_published_at=None,
                    updated_at=datetime.now(),
                )
            )
            await self.session.commit()
            await self.session.refresh(job)
        return job

    async def list_due(self, *, now: datetime) -> list[DeliveryJob]:
        publish_is_stale = or_(
            DeliveryJob.last_published_at.is_(None),
            DeliveryJob.last_published_at <= now - timedelta(seconds=60),
        )
        statement = (
            select(DeliveryJob)
            .where(
                or_(
                    and_(
                        DeliveryJob.status == "pending",
                        or_(
                            DeliveryJob.next_attempt_at.is_(None),
                            DeliveryJob.next_attempt_at <= now,
                        ),
                        publish_is_stale,
                    ),
                    and_(
                        DeliveryJob.status == "retry",
                        or_(
                            DeliveryJob.next_attempt_at.is_(None),
                            DeliveryJob.next_attempt_at <= now,
                        ),
                        or_(
                            DeliveryJob.last_published_at.is_(None),
                            DeliveryJob.last_published_at < DeliveryJob.next_attempt_at,
                        ),
                    ),
                    and_(
                        DeliveryJob.status == "processing",
                        DeliveryJob.updated_at <= now - timedelta(seconds=60),
                        or_(
                            DeliveryJob.last_published_at.is_(None),
                            DeliveryJob.last_published_at < DeliveryJob.updated_at,
                        ),
                    ),
                ),
            )
            .order_by(DeliveryJob.job_id)
        )
        result = await self.session.execute(statement)
        return list(result.scalars())

    async def mark_published(self, job_id: int, *, published_at: datetime) -> None:
        await self.session.execute(
            update(DeliveryJob)
            .where(DeliveryJob.job_id == job_id)
            .values(last_published_at=published_at, updated_at=DeliveryJob.updated_at)
        )
        await self.session.commit()

    async def mark_retry(
        self,
        job_id: int,
        *,
        lease_token: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        result = await self.session.execute(
            update(DeliveryJob)
            .where(
                DeliveryJob.job_id == job_id,
                DeliveryJob.status == "processing",
                DeliveryJob.lease_token == lease_token,
            )
            .values(
                status="retry",
                last_error=error,
                next_attempt_at=next_attempt_at,
                lease_token=None,
                updated_at=datetime.now(),
            )
            .returning(DeliveryJob.job_id)
        )
        await self.session.commit()
        return result.scalar_one_or_none() is not None

    async def mark_succeeded(
        self, job_id: int, *, lease_token: str, result_message_ids: list[int]
    ) -> bool:
        result = await self.session.execute(
            update(DeliveryJob)
            .where(
                DeliveryJob.job_id == job_id,
                DeliveryJob.status == "processing",
                DeliveryJob.lease_token == lease_token,
            )
            .values(
                status="succeeded",
                result_message_ids=result_message_ids,
                last_error=None,
                next_attempt_at=None,
                lease_token=None,
                updated_at=datetime.now(),
            )
            .returning(DeliveryJob.job_id)
        )
        await self.session.commit()
        return result.scalar_one_or_none() is not None

    async def mark_failed(self, job_id: int, *, lease_token: str, error: str) -> bool:
        result = await self.session.execute(
            update(DeliveryJob)
            .where(
                DeliveryJob.job_id == job_id,
                DeliveryJob.status == "processing",
                DeliveryJob.lease_token == lease_token,
            )
            .values(
                status="failed",
                last_error=error,
                next_attempt_at=None,
                lease_token=None,
                updated_at=datetime.now(),
            )
            .returning(DeliveryJob.job_id)
        )
        await self.session.commit()
        return result.scalar_one_or_none() is not None


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
