from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Booking, BookingStatus, Specialist, User


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    full_name: str,
    username: str | None,
    *,
    is_admin: bool = False,
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        from app.database.models import Role

        user = User(
            telegram_id=telegram_id,
            full_name=full_name,
            username=username,
            role=Role.ADMIN if is_admin else Role.CLIENT,
        )
        session.add(user)
    else:
        user.full_name, user.username = full_name, username
    await session.commit()
    await session.refresh(user)
    return user


async def user_bookings(session: AsyncSession, user_id, active: bool = True) -> list[Booking]:
    query = (
        select(Booking)
        .options(
            selectinload(Booking.service),
            selectinload(Booking.specialist).selectinload(Specialist.user),
        )
        .where(Booking.client_id == user_id)
    )
    if active:
        query = query.where(Booking.status == BookingStatus.CONFIRMED)
    return list((await session.scalars(query.order_by(Booking.starts_at))).all())
