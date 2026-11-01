import hashlib
import secrets
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.database.models import (
    Booking,
    Role,
    Service,
    Specialist,
    SpecialistInvitation,
    SpecialistService,
    User,
    WeeklySchedule,
)
from app.database.session import SessionFactory

router = Router()


def is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in get_settings().admin_ids)


@router.message(Command("admin_help"))
async def admin_help(message: Message) -> None:
    if not is_admin(message):
        return
    await message.answer(
        "Admin commands:\n"
        "/addservice Name | description | minutes | price\n"
        "/services\n/invite_specialist Bio | timezone\n"
        "/join_specialist CODE (send this privately to the bot)\n"
        "/addspecialist Telegram_ID | bio | timezone (legacy)\n"
        "/specialists\n/schedule (interactive weekly schedule editor)\n"
        "/assignservice Specialist_ID | Service_ID\n"
        "/setschedule Specialist_ID | weekday(0=Mon) | start | end | break_start-break_end\n"
        "/today [YYYY-MM-DD]\n/clients"
    )


@router.message(Command("invite_specialist"))
async def invite_specialist(message: Message) -> None:
    if not is_admin(message):
        await message.answer("Access denied.")
        return
    parts = (message.text or "").partition(" ")[2].split("|")
    if len(parts) != 2:
        await message.answer("Usage: /invite_specialist Bio | timezone")
        return
    bio, timezone_name = (part.strip() for part in parts)
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        await message.answer("Unknown timezone. Use an IANA name such as Europe/Minsk.")
        return
    code = secrets.token_hex(6).upper()
    invitation = SpecialistInvitation(
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        bio=bio,
        timezone=timezone_name,
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    async with SessionFactory() as session:
        session.add(invitation)
        await session.commit()
    await message.answer(
        "Specialist invite created (valid for 48 hours). "
        "Send this code privately to the specialist:\n"
        f"<code>{code}</code>\n\nThey must send /join_specialist followed by the code to this bot."
    )


@router.message(Command("addservice"))
async def add_service(message: Message) -> None:
    if not is_admin(message):
        await message.answer("Access denied.")
        return
    parts = (message.text or "").partition(" ")[2].split("|")
    if len(parts) != 4:
        await message.answer("Usage: /addservice Name | description | minutes | price")
        return
    try:
        name, description, minutes, price = [part.strip() for part in parts]
        duration = int(minutes)
        amount = Decimal(price)
        if duration <= 0 or amount < 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        await message.answer("Duration and price must be valid positive numbers.")
        return
    async with SessionFactory() as session:
        session.add(
            Service(
                name=name[:120], description=description, duration_minutes=duration, price=amount
            )
        )
        await session.commit()
    await message.answer(f"Service '{name}' added.")


@router.message(Command("services"))
async def list_services(message: Message) -> None:
    if not is_admin(message):
        return
    async with SessionFactory() as session:
        services = list((await session.scalars(select(Service).order_by(Service.name))).all())
    await message.answer(
        "\n".join(f"{s.id} | {s.name} | {s.price} | active={s.is_active}" for s in services)
        or "No services."
    )


@router.message(Command("addspecialist"))
async def add_specialist(message: Message) -> None:
    if not is_admin(message):
        await message.answer("Access denied.")
        return
    parts = (message.text or "").partition(" ")[2].split("|")
    if len(parts) != 3:
        await message.answer("Usage: /addspecialist Telegram_ID | bio | timezone")
        return
    try:
        telegram_id = int(parts[0].strip())
        bio = parts[1].strip()
        timezone_name = parts[2].strip()
        ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        await message.answer(
            "Telegram ID must be a number and timezone a valid IANA name, e.g. Europe/Minsk."
        )
        return
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            await message.answer(
                "That person must open the bot and send /start before being added."
            )
            return
        existing = await session.scalar(select(Specialist).where(Specialist.user_id == user.id))
        if existing:
            await message.answer(f"Already a specialist. ID: {existing.id}")
            return
        user.role = Role.SPECIALIST
        specialist = Specialist(user_id=user.id, bio=bio, timezone=timezone_name)
        session.add(specialist)
        await session.commit()
        await session.refresh(specialist)
    await message.answer(f"Specialist added. ID: {specialist.id}")


@router.message(Command("specialists"))
async def list_specialists(message: Message) -> None:
    if not is_admin(message):
        return
    async with SessionFactory() as session:
        rows = list(
            (
                await session.scalars(
                    select(Specialist)
                    .options(selectinload(Specialist.user))
                    .join(User)
                    .order_by(User.full_name)
                )
            ).all()
        )
        output = [
            f"{row.id} | {row.user.full_name} | {row.timezone} | active={row.is_active}"
            for row in rows
        ]
    await message.answer("\n".join(output) or "No specialists.")


@router.message(Command("assignservice"))
async def assign_service(message: Message) -> None:
    if not is_admin(message):
        await message.answer("Access denied.")
        return
    parts = (message.text or "").partition(" ")[2].split("|")
    if len(parts) != 2:
        await message.answer("Usage: /assignservice Specialist_ID | Service_ID")
        return
    try:
        specialist_id, service_id = (UUID(part.strip()) for part in parts)
    except ValueError:
        await message.answer("IDs must be UUIDs copied from /specialists and /services.")
        return
    async with SessionFactory() as session:
        specialist = await session.get(Specialist, specialist_id)
        service = await session.get(Service, service_id)
        if not specialist or not service:
            await message.answer("Specialist or service not found.")
            return
        session.add(SpecialistService(specialist_id=specialist_id, service_id=service_id))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            await message.answer("This service is already assigned to that specialist.")
            return
    await message.answer("Service assigned to specialist.")


@router.message(Command("setschedule"))
async def set_schedule(message: Message) -> None:
    if not is_admin(message):
        await message.answer("Access denied.")
        return
    parts = (message.text or "").partition(" ")[2].split("|")
    if len(parts) != 5:
        await message.answer(
            "Usage: /setschedule Specialist_ID | weekday(0=Mon) | start | end | "
            "break_start-break_end (or -)"
        )
        return
    try:
        specialist_id = UUID(parts[0].strip())
        weekday = int(parts[1].strip())
        start_time = time.fromisoformat(parts[2].strip())
        end_time = time.fromisoformat(parts[3].strip())
        break_value = parts[4].strip()
        if weekday not in range(7) or start_time >= end_time:
            raise ValueError
        if break_value == "-":
            break_start = break_end = None
        else:
            values = break_value.split("-")
            if len(values) != 2:
                raise ValueError
            break_start, break_end = (time.fromisoformat(value.strip()) for value in values)
            if not (start_time <= break_start < break_end <= end_time):
                raise ValueError
    except ValueError:
        await message.answer(
            "Invalid schedule. Use weekday 0-6 and times HH:MM; break must fit within work hours."
        )
        return
    async with SessionFactory() as session:
        if not await session.get(Specialist, specialist_id):
            await message.answer("Specialist not found. Check /specialists.")
            return
        row = await session.scalar(
            select(WeeklySchedule).where(
                WeeklySchedule.specialist_id == specialist_id,
                WeeklySchedule.weekday == weekday,
            )
        )
        if row is None:
            row = WeeklySchedule(
                specialist_id=specialist_id,
                weekday=weekday,
                start_time=start_time,
                end_time=end_time,
                break_start=break_start,
                break_end=break_end,
            )
            session.add(row)
        else:
            row.start_time, row.end_time = start_time, end_time
            row.break_start, row.break_end = break_start, break_end
        await session.commit()
    await message.answer(f"Schedule saved for weekday {weekday}.")


@router.message(Command("today"))
async def list_bookings(message: Message) -> None:
    if not is_admin(message):
        await message.answer("Access denied.")
        return
    parts = (message.text or "").split()
    try:
        day = date.fromisoformat(parts[1]) if len(parts) > 1 else date.today()
    except ValueError:
        await message.answer("Date must use YYYY-MM-DD.")
        return
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    async with SessionFactory() as session:
        rows = list(
            (
                await session.scalars(
                    select(Booking)
                    .where(Booking.starts_at >= start, Booking.starts_at < end)
                    .order_by(Booking.starts_at)
                )
            ).all()
        )
    await message.answer(
        "\n".join(f"{b.starts_at:%H:%M} UTC · {b.id} · {b.status.value}" for b in rows)
        or "No bookings."
    )


@router.message(Command("clients"))
async def list_clients(message: Message) -> None:
    if not is_admin(message):
        return
    async with SessionFactory() as session:
        rows = list(
            (
                await session.scalars(
                    select(User).where(User.role == Role.CLIENT).order_by(User.full_name).limit(100)
                )
            ).all()
        )
    await message.answer(
        "\n".join(f"{u.full_name} · @{u.username or '—'} · {u.telegram_id}" for u in rows)
        or "No clients."
    )
