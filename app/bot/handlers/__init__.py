from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.admin_schedule import router as schedule_router
from app.bot.handlers.client import router as client_router

__all__ = ["admin_router", "client_router", "schedule_router"]
