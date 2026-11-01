import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import Booking, Notification, Service, Specialist, User

logger = logging.getLogger(__name__)


class NotificationService:
    """Persist notifications independently from booking transactions and retry failures."""

    def __init__(self, bot: Bot, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.bot, self.sessions = bot, sessions

    async def queue_booking_messages(self, booking_id) -> None:
        async with self.sessions() as session:
            booking = await session.get(Booking, booking_id)
            if not booking:
                return
            client = await session.get(User, booking.client_id)
            specialist = await session.get(Specialist, booking.specialist_id)
            service = await session.get(Service, booking.service_id)
            if client:
                session.add(
                    Notification(
                        booking_id=booking.id,
                        recipient_telegram_id=client.telegram_id,
                        message=(
                            f"Booking confirmed: {service.name if service else 'service'} at "
                            f"{booking.starts_at:%Y-%m-%d %H:%M} UTC."
                        ),
                        kind="confirmation",
                        due_at=datetime.now(UTC),
                    )
                )
            if specialist:
                user = await session.get(User, specialist.user_id)
                if user:
                    session.add(
                        Notification(
                            booking_id=booking.id,
                            recipient_telegram_id=user.telegram_id,
                            message=f"New booking at {booking.starts_at:%Y-%m-%d %H:%M} UTC.",
                            kind="new_booking",
                            due_at=datetime.now(UTC),
                        )
                    )
            reminder_at = max(
                booking.starts_at - timedelta(hours=1),
                datetime.now(UTC),
            )
            session.add(
                Notification(
                    booking_id=booking.id,
                    recipient_telegram_id=client.telegram_id,
                    message=(
                        f"Reminder: your appointment starts at "
                        f"{booking.starts_at:%Y-%m-%d %H:%M} UTC."
                    ),
                    kind="reminder",
                    due_at=reminder_at,
                )
            )
            await session.commit()

    async def deliver_due(self, limit: int = 50) -> None:
        async with self.sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Notification)
                        .where(
                            Notification.sent_at.is_(None),
                            Notification.due_at <= datetime.now(UTC),
                        )
                        .order_by(Notification.due_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for row in rows:
                try:
                    await self.bot.send_message(row.recipient_telegram_id, row.message)
                    row.sent_at = datetime.now(UTC)
                except Exception as exc:
                    row.attempts += 1
                    row.last_error = type(exc).__name__
                    row.due_at = datetime.now(UTC) + timedelta(minutes=min(2**row.attempts, 360))
                    logger.warning(
                        "Notification delivery failed (id=%s, attempt=%d)", row.id, row.attempts
                    )
            await session.commit()
