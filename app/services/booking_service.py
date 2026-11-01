from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EntityNotFound, SlotUnavailable
from app.database.models import (
    Booking,
    BookingStatus,
    ScheduleException,
    Service,
    Specialist,
    SpecialistService,
    User,
    WeeklySchedule,
)


class BookingService:
    """Booking use cases. PostgreSQL's exclusion constraint is the final concurrency guard."""

    def __init__(self, slot_minutes: int = 15) -> None:
        self.slot_minutes = slot_minutes

    async def available_slots(
        self, session: AsyncSession, service_id: UUID, specialist_id: UUID, day: date
    ) -> list[datetime]:
        service = await session.get(Service, service_id)
        specialist = await session.get(Specialist, specialist_id)
        if not service or not service.is_active or not specialist or not specialist.is_active:
            return []
        if not await session.scalar(
            select(SpecialistService).where(
                SpecialistService.service_id == service_id,
                SpecialistService.specialist_id == specialist_id,
            )
        ):
            return []
        tz = ZoneInfo(specialist.timezone)
        weekday = await session.scalar(
            select(WeeklySchedule).where(
                WeeklySchedule.specialist_id == specialist_id,
                WeeklySchedule.weekday == day.weekday(),
            )
        )
        if not weekday:
            return []
        exception = await session.scalar(
            select(ScheduleException).where(
                ScheduleException.specialist_id == specialist_id,
                ScheduleException.date >= datetime.combine(day, time.min, tzinfo=tz),
                ScheduleException.date
                < datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz),
            )
        )
        if exception and exception.is_day_off:
            return []
        start = datetime.combine(
            day,
            exception.start_time if exception and exception.start_time else weekday.start_time,
            tzinfo=tz,
        )
        end = datetime.combine(
            day,
            exception.end_time if exception and exception.end_time else weekday.end_time,
            tzinfo=tz,
        )
        query_start, query_end = start.astimezone(ZoneInfo("UTC")), end.astimezone(ZoneInfo("UTC"))
        busy = (
            await session.scalars(
                select(Booking).where(
                    Booking.specialist_id == specialist_id,
                    Booking.status == BookingStatus.CONFIRMED,
                    Booking.starts_at < query_end,
                    Booking.ends_at > query_start,
                )
            )
        ).all()
        slots: list[datetime] = []
        cursor = start
        duration = timedelta(minutes=service.duration_minutes)
        while cursor + duration <= end:
            slot_end = cursor + duration
            if weekday.break_start and weekday.break_end:
                br_start = datetime.combine(day, weekday.break_start, tzinfo=tz)
                br_end = datetime.combine(day, weekday.break_end, tzinfo=tz)
                if cursor < br_end and slot_end > br_start:
                    cursor += timedelta(minutes=self.slot_minutes)
                    continue
            if all(
                slot_end.astimezone(ZoneInfo("UTC")) <= b.starts_at
                or cursor.astimezone(ZoneInfo("UTC")) >= b.ends_at
                for b in busy
            ):
                if cursor > datetime.now(tz):
                    slots.append(cursor)
            cursor += timedelta(minutes=self.slot_minutes)
        return slots

    async def create_booking(
        self,
        session: AsyncSession,
        *,
        client_id: UUID,
        service_id: UUID,
        specialist_id: UUID,
        starts_at: datetime,
    ) -> Booking:
        service = await session.get(Service, service_id)
        specialist = await session.get(Specialist, specialist_id)
        client = await session.get(User, client_id)
        if not service or not specialist or not client:
            raise EntityNotFound("Client, service, or specialist does not exist")
        if starts_at.tzinfo is None:
            raise ValueError("starts_at must include a timezone")
        local_start = starts_at.astimezone(ZoneInfo(specialist.timezone))
        slots = await self.available_slots(session, service_id, specialist_id, local_start.date())
        if local_start not in slots:
            raise SlotUnavailable("The selected time is no longer available")
        starts_utc = starts_at.astimezone(ZoneInfo("UTC"))
        ends_utc = starts_utc + timedelta(minutes=service.duration_minutes)
        booking = Booking(
            client_id=client_id,
            service_id=service_id,
            specialist_id=specialist_id,
            starts_at=starts_utc,
            ends_at=ends_utc,
            status=BookingStatus.CONFIRMED,
        )
        session.add(booking)
        try:
            await session.commit()
            await session.refresh(booking)
        except IntegrityError as exc:
            await session.rollback()
            raise SlotUnavailable("The selected time was booked concurrently") from exc
        return booking

    async def cancel_booking(
        self, session: AsyncSession, booking_id: UUID, actor_id: UUID, *, is_admin: bool = False
    ) -> Booking:
        booking = await session.get(Booking, booking_id)
        if not booking:
            raise EntityNotFound("Booking not found")
        if booking.client_id != actor_id and not is_admin:
            from app.core.exceptions import AccessDenied

            raise AccessDenied("Cannot cancel another client's booking")
        if booking.status != BookingStatus.CONFIRMED:
            raise ValueError("Only confirmed bookings can be cancelled")
        booking.status = BookingStatus.CANCELLED
        await session.commit()
        await session.refresh(booking)
        return booking
