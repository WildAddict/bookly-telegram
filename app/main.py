import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.handlers import admin_router, client_router, schedule_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import SessionFactory, engine
from app.services.notification_service import NotificationService


async def main() -> None:
    configure_logging()
    settings = get_settings()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(client_router)
    dispatcher.include_router(admin_router)
    dispatcher.include_router(schedule_router)
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        NotificationService(bot, SessionFactory).deliver_due,
        "interval",
        seconds=settings.reminder_poll_seconds,
        id="notifications",
        max_instances=1,
    )
    scheduler.start()
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Bookly stopped")
