from datetime import time
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.database.models import Specialist, WeeklySchedule
from app.database.session import SessionFactory

router = Router()
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class ScheduleFlow(StatesGroup):
    choose_day = State()
    enter_hours = State()
    choose_break = State()
    enter_break = State()


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in get_settings().admin_ids


def _specialist_keyboard(rows: list[Specialist]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=row.user.full_name, callback_data=f"sched:sp:{row.id}")]
            for row in rows
        ]
    )


def _week_keyboard(
    specialist_id: UUID, schedule: dict[int, WeeklySchedule]
) -> InlineKeyboardMarkup:
    buttons = []
    for weekday, name in enumerate(WEEKDAYS):
        row = schedule.get(weekday)
        label = (
            f"{name}: {row.start_time:%H:%M}–{row.end_time:%H:%M}" if row else f"{name}: day off"
        )
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"sched:day:{weekday}")])
    buttons.append([InlineKeyboardButton(text="Done", callback_data="sched:done")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _day_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Set working hours", callback_data="sched:hours")],
            [InlineKeyboardButton(text="Set day off", callback_data="sched:off")],
            [InlineKeyboardButton(text="Back to week", callback_data="sched:week")],
        ]
    )


def _break_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="No break", callback_data="sched:break:none"),
                InlineKeyboardButton(text="Add break", callback_data="sched:break:add"),
            ]
        ]
    )


async def _show_week(
    message: Message | None, callback: CallbackQuery | None, state: FSMContext, specialist_id: UUID
) -> None:
    async with SessionFactory() as session:
        specialist = await session.scalar(
            select(Specialist)
            .options(selectinload(Specialist.user))
            .where(Specialist.id == specialist_id)
        )
        rows = list(
            (
                await session.scalars(
                    select(WeeklySchedule).where(WeeklySchedule.specialist_id == specialist_id)
                )
            ).all()
        )
    if specialist is None:
        if callback:
            await callback.answer("Specialist not found", show_alert=True)
        return
    await state.set_state(ScheduleFlow.choose_day)
    await state.update_data(specialist_id=str(specialist_id))
    text = (
        f"Weekly schedule for {specialist.user.full_name} ({specialist.timezone}).\n"
        "Choose a day to edit:"
    )
    keyboard = _week_keyboard(specialist_id, {row.weekday: row for row in rows})
    if callback and callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()
    elif message:
        await message.answer(text, reply_markup=keyboard)


@router.message(Command("schedule"))
async def begin_schedule(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Access denied.")
        return
    await state.clear()
    async with SessionFactory() as session:
        rows = list(
            (
                await session.scalars(
                    select(Specialist)
                    .options(selectinload(Specialist.user))
                    .where(Specialist.is_active.is_(True))
                )
            ).all()
        )
    if not rows:
        await message.answer("No active specialists. Add one with /addspecialist first.")
        return
    await message.answer("Choose a specialist:", reply_markup=_specialist_keyboard(rows))


@router.callback_query(F.data.startswith("sched:sp:"))
async def choose_specialist(query: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Access denied", show_alert=True)
        return
    specialist_id = UUID(query.data.rsplit(":", 1)[1])
    await _show_week(None, query, state, specialist_id)


@router.callback_query(ScheduleFlow.choose_day, F.data.startswith("sched:day:"))
async def choose_day(query: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Access denied", show_alert=True)
        return
    weekday = int(query.data.rsplit(":", 1)[1])
    await state.update_data(weekday=weekday)
    await state.set_state(ScheduleFlow.choose_day)
    await query.message.edit_text(
        f"{WEEKDAYS[weekday]}: choose an action.", reply_markup=_day_keyboard()
    )
    await query.answer()


@router.callback_query(F.data == "sched:hours")
async def request_hours(query: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Access denied", show_alert=True)
        return
    await state.set_state(ScheduleFlow.enter_hours)
    await query.message.edit_text("Send working hours as HH:MM-HH:MM, for example 09:00-18:00.")
    await query.answer()


@router.message(ScheduleFlow.enter_hours)
async def receive_hours(message: Message, state: FSMContext) -> None:
    try:
        parts = (message.text or "").split("-")
        if len(parts) != 2:
            raise ValueError
        start, end = (time.fromisoformat(part.strip()) for part in parts)
        if start >= end:
            raise ValueError
    except ValueError:
        await message.answer("Invalid hours. Send HH:MM-HH:MM, for example 09:00-18:00.")
        return
    await state.update_data(start_time=start.isoformat(), end_time=end.isoformat())
    await state.set_state(ScheduleFlow.choose_break)
    await message.answer("Add a break or save without one?", reply_markup=_break_keyboard())


async def _save_schedule(
    state: FSMContext, break_start: time | None, break_end: time | None
) -> UUID:
    data = await state.get_data()
    specialist_id, weekday = UUID(data["specialist_id"]), int(data["weekday"])
    start, end = time.fromisoformat(data["start_time"]), time.fromisoformat(data["end_time"])
    async with SessionFactory() as session:
        row = await session.scalar(
            select(WeeklySchedule).where(
                WeeklySchedule.specialist_id == specialist_id, WeeklySchedule.weekday == weekday
            )
        )
        if row is None:
            row = WeeklySchedule(
                specialist_id=specialist_id,
                weekday=weekday,
                start_time=start,
                end_time=end,
                break_start=break_start,
                break_end=break_end,
            )
            session.add(row)
        else:
            row.start_time, row.end_time = start, end
            row.break_start, row.break_end = break_start, break_end
        await session.commit()
    return specialist_id


@router.callback_query(ScheduleFlow.choose_break, F.data == "sched:break:none")
async def save_without_break(query: CallbackQuery, state: FSMContext) -> None:
    specialist_id = await _save_schedule(state, None, None)
    await _show_week(None, query, state, specialist_id)


@router.callback_query(ScheduleFlow.choose_break, F.data == "sched:break:add")
async def request_break(query: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Access denied", show_alert=True)
        return
    await state.set_state(ScheduleFlow.enter_break)
    await query.message.edit_text("Send break as HH:MM-HH:MM, for example 13:00-14:00.")
    await query.answer()


@router.message(ScheduleFlow.enter_break)
async def receive_break(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        parts = (message.text or "").split("-")
        if len(parts) != 2:
            raise ValueError
        start, end = (time.fromisoformat(part.strip()) for part in parts)
        work_start = time.fromisoformat(data["start_time"])
        work_end = time.fromisoformat(data["end_time"])
        if not (work_start <= start < end <= work_end):
            raise ValueError
    except ValueError:
        await message.answer(
            "Break must fit inside working hours. Send HH:MM-HH:MM, for example 13:00-14:00."
        )
        return
    specialist_id = await _save_schedule(state, start, end)
    await _show_week(message, None, state, specialist_id)


@router.callback_query(F.data == "sched:off")
async def set_day_off(query: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Access denied", show_alert=True)
        return
    data = await state.get_data()
    specialist_id, weekday = UUID(data["specialist_id"]), int(data["weekday"])
    async with SessionFactory() as session:
        row = await session.scalar(
            select(WeeklySchedule).where(
                WeeklySchedule.specialist_id == specialist_id, WeeklySchedule.weekday == weekday
            )
        )
        if row:
            await session.delete(row)
            await session.commit()
    await _show_week(None, query, state, specialist_id)


@router.callback_query(F.data == "sched:week")
async def return_to_week(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _show_week(None, query, state, UUID(data["specialist_id"]))


@router.callback_query(F.data == "sched:done")
async def finish_schedule(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.message.edit_text("Schedule saved.")
    await query.answer()
