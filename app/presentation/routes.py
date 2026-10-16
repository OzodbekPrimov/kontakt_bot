from aiogram import Router

from app.presentation.routers import admin, lookup, support, user


def create_router() -> Router:
    router = Router(name="application")
    router.include_routers(
        user.create_router(), admin.create_router(), support.create_router(), lookup.create_router()
    )
    return router
