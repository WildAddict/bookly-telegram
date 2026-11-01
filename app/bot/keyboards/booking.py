from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def choices(items, prefix: str, label) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label(item), callback_data=f"{prefix}:{item.id}")]
            for item in items
        ]
    )


def dates(days) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=day.strftime("%a %d %b"), callback_data=f"day:{day.isoformat()}"
                )
            ]
            for day in days
        ]
    )


def slots(items) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=item.strftime("%H:%M"), callback_data=f"slot:{item.isoformat()}"
                )
            ]
            for item in items
        ]
    )
