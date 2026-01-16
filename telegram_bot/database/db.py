"""Подключение и инициализация базы данных."""
# Реэкспорт для обратной совместимости
from database.models import Base, engine, async_session_maker, init_db
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session() -> AsyncSession:
    """Получает сессию базы данных."""
    async with async_session_maker() as session:
        yield session
