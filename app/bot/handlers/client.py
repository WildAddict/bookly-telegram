import hashlib
from datetime import UTC, date, datetime, timedelta
from html import escape
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot.keyboards.booking import choices, dates, slots
from app.core.config import get_settings
from app.core.exceptions import SlotUnavailable
from app.database.models import (
    Booking,
    BookingStatus,
    Role,
    Service,
    Specialist,
    SpecialistInvitation,
    SpecialistService,
    User,
)
from app.database.session import SessionFactory
from app.services.booking_service import BookingService
from app.services.notification_service import NotificationService
from app.services.user_service import get_or_create_user, user_bookings

router = Router()
booking_service = BookingService(get_settings().booking_slot_minutes)


class BookingFlow(StatesGroup):
    service = State()
    specialist = State()
    day = State()
    slot = State()


@router.message(CommandStart())
async def start(message: Message) -> None:
    assert message.from_user
    async with SessionFactory() as session:
        await get_or_create_user(
            session,
            message.from_user.id,
            message.from_user.full_name,
            message.from_user.username,
            is_admin=message.from_user.id in get_settings().admin_ids,
        )
    await message.answer(
        f"Welcome to {get_settings().business_name}!\n"
        "/book — make an appointment\n/mybookings — view your bookings"
    )


@router.message(Command("join_specialist"))
async def join_as_specialist(message: Message) -> None:
    """Claim a single-use administrator invitation in a private bot chat."""
    if not message.from_user:
        return
    if message.chat.type != "private":
        await message.answer("Please claim your invitation in a private chat with this bot.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /join_specialist CODE")
        return
    code_hash = hashlib.sha256(parts[1].strip().upper().encode()).hexdigest()
    async with SessionFactory() as session:
        invitation = await session.scalar(
            select(SpecialistInvitation)
            .where(
                SpecialistInvitation.code_hash == code_hash,
                SpecialistInvitation.claimed_at.is_(None),
                SpecialistInvitation.expires_at > datetime.now(UTC),
            )
            .with_for_update()
        )
        if invitation is None:
            await message.answer(
                "This invitation is invalid, expired, or already used. "
                "Ask the admin for a new code."
            )
            return
        user = await session.scalar(select(User).where(User.telegram_id == message.from_user.id))
        if user is None:
            user = User(
                telegram_id=message.from_user.id,
                full_name=message.from_user.full_name,
                username=message.from_user.username,
                role=Role.SPECIALIST,
            )
            session.add(user)
            await session.flush()
        else:
            existing = await session.scalar(select(Specialist).where(Specialist.user_id == user.id))
            if existing:
                await message.answer("This Telegram account is already registered as a specialist.")
                return
            user.full_name = message.from_user.full_name
            user.username = message.from_user.username
            user.role = Role.SPECIALIST
        session.add(Specialist(user_id=user.id, bio=invitation.bio, timezone=invitation.timezone))
        invitation.claimed_at = datetime.now(UTC)
        await session.commit()
    await message.answer(
        "Your specialist account is ready. The administrator will assign your services "
        "and set your schedule."
    )


@router.message(Command("book"))
async def begin_booking(message: Message, state: FSMContext) -> None:
    async with SessionFactory() as session:
        items = list(
            (
                await session.scalars(
                    select(Service).where(Service.is_active.is_(True)).order_by(Service.name)
                )
            ).all()
        )
    if not items:
        await message.answer("No services are available yet.")
        return
    await state.set_state(BookingFlow.service)
    await message.answer(
        "Choose a service:",
        reply_markup=choices(
            items, "service", lambda x: f"{x.name} · {x.price} · {x.duration_minutes} min"
        ),
    )


@router.callback_query(BookingFlow.service, F.data.startswith("service:"))
async def choose_service(query: CallbackQuery, state: FSMContext) -> None:
    service_id = UUID(query.data.split(":", 1)[1])
    async with SessionFactory() as session:
        items = list(
            (
                await session.scalars(
                    select(Specialist)
                    .options(selectinload(Specialist.user))
                    .join(SpecialistService)
                    .where(
                        SpecialistService.service_id == service_id, Specialist.is_active.is_(True)
                    )
                )
            ).all()
        )
    if not items:
        await query.answer("No specialists offer this service", show_alert=True)
        return
    await state.update_data(service_id=str(service_id))
    await state.set_state(BookingFlow.specialist)
    await query.message.edit_text(
        "Choose a specialist:",
        reply_markup=choices(
            items, "specialist", lambda x: x.user.full_name if x.user else "Specialist"
        ),
    )
    await query.answer()


@router.callback_query(BookingFlow.specialist, F.data.startswith("specialist:"))
async def choose_specialist(query: CallbackQuery, state: FSMContext) -> None:
    specialist_id = UUID(query.data.split(":", 1)[1])
    await state.update_data(specialist_id=str(specialist_id))
    await state.set_state(BookingFlow.day)
    await query.message.edit_text(
        "Choose a date:",
        reply_markup=dates([date.today() + timedelta(days=i) for i in range(1, 15)]),
    )
    await query.answer()


@router.callback_query(BookingFlow.day, F.data.startswith("day:"))
async def choose_day(query: CallbackQuery, state: FSMContext) -> None:
    selected_day = date.fromisoformat(query.data.split(":", 1)[1])
    data = await state.get_data()
    async with SessionFactory() as session:
        avail = await booking_service.available_slots(
            session, UUID(data["service_id"]), UUID(data["specialist_id"]), selected_day
        )
    await state.update_data(day=selected_day.isoformat())
    await state.set_state(BookingFlow.slot)
    if not avail:
        await query.message.edit_text("No times available. Use /book to choose another day.")
    else:
        await query.message.edit_text(
            "Available times (specialist local time):", reply_markup=slots(avail)
        )
    await query.answer()


@router.callback_query(BookingFlow.slot, F.data.startswith("slot:"))
async def choose_slot(query: CallbackQuery, state: FSMContext) -> None:
    assert query.from_user
    data = await state.get_data()
    async with SessionFactory() as session:
        user = await get_or_create_user(
            session,
            query.from_user.id,
            query.from_user.full_name,
            query.from_user.username,
            is_admin=query.from_user.id in get_settings().admin_ids,
        )
        try:
            booking = await booking_service.create_booking(
                session,
                client_id=user.id,
                service_id=UUID(data["service_id"]),
                specialist_id=UUID(data["specialist_id"]),
                starts_at=datetime.fromisoformat(query.data.split(":", 1)[1]),
            )
        except (SlotUnavailable, ValueError) as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await NotificationService(_bot(query), SessionFactory).queue_booking_messages(booking.id)
    await state.clear()
    await query.message.edit_text(
        "Your booking is confirmed. You will receive a reminder before the appointment."
    )
    await query.answer()


def _bot(query: CallbackQuery):
    return query.bot


@router.message(Command("mybookings"))
async def my_bookings(message: Message) -> None:
    assert message.from_user
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == message.from_user.id))
        if not user:
            await message.answer("Send /start first.")
            return
        records = await user_bookings(session, user.id)
        if not records:
            await message.answer("У вас пока нет записей.")
            return
        now = datetime.now(UTC)
        upcoming = [record for record in records if record.starts_at >= now]
        past = [record for record in records if record.starts_at < now]

        def format_booking(record: Booking) -> str:
            specialist = record.specialist
            local_start = record.starts_at.astimezone(ZoneInfo(specialist.timezone))
            local_end = record.ends_at.astimezone(ZoneInfo(specialist.timezone))
            status_labels = {
                BookingStatus.CONFIRMED: "Подтверждена",
                BookingStatus.CANCELLED: "Отменена",
                BookingStatus.COMPLETED: "Завершена",
                BookingStatus.NO_SHOW: "Неявка",
            }
            return (
                f"✂️ <b>{escape(record.service.name)}</b>\n"
                f"👤 Специалист: {escape(specialist.user.full_name)}\n"
                f"📅 {local_start:%d.%m.%Y} · {local_start:%H:%M}–{local_end:%H:%M} "
                f"({escape(specialist.timezone)})\n"
                f"💳 Стоимость: {record.service.price}\n"
                f"Статус: {status_labels[record.status]}\n"
                f"ID для отмены: <code>{record.id}</code>"
            )

        sections = []
        if upcoming:
            sections.append(
                "<b>Предстоящие записи</b>\n\n" + "\n\n".join(map(format_booking, upcoming))
            )
        if past:
            sections.append("<b>Прошедшие записи</b>\n\n" + "\n\n".join(map(format_booking, past)))
        await message.answer("\n\n".join(sections) + "\n\nДля отмены: /cancel и ID записи.")


@router.message(Command("cancel"))
async def cancel_booking(message: Message) -> None:
    assert message.from_user
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /cancel followed by the booking ID")
        return
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == message.from_user.id))
        try:
            booking_id = UUID(parts[1])
        except ValueError:
            await message.answer("Invalid booking ID.")
            return
        if not user:
            await message.answer("Send /start first.")
            return
        from app.core.exceptions import AccessDenied, EntityNotFound
        from app.services.booking_service import BookingService

        try:
            await BookingService().cancel_booking(
                session,
                booking_id,
                user.id,
                is_admin=message.from_user.id in get_settings().admin_ids,
            )
        except (AccessDenied, EntityNotFound, ValueError) as exc:
            await message.answer(str(exc))
            return
    await message.answer("Booking cancelled.")
