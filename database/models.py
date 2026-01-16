"""Модели базы данных с использованием SQLAlchemy 2.0 и async поддержкой."""
from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config import DATABASE_URL


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""
    pass


class User(Base):
    """Модель пользователя."""
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_message_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Связи
    messages: Mapped[list["Message"]] = relationship("Message", back_populates="user", cascade="all, delete-orphan")


class Message(Base):
    """Модель сообщения."""
    __tablename__ = "messages"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_type: Mapped[str] = mapped_column(String(50), nullable=False)  # text, photo, audio
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Связи
    user: Mapped["User"] = relationship("User", back_populates="messages")
    responses: Mapped[list["Response"]] = relationship("Response", back_populates="message", cascade="all, delete-orphan")


class Response(Base):
    """Модель ответа бота."""
    __tablename__ = "responses"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(Integer, ForeignKey("messages.id"), nullable=False, index=True)
    bot_response: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Связи
    message: Mapped["Message"] = relationship("Message", back_populates="responses")


# Создание движка и сессии
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def init_db():
    """Инициализирует базу данных (создает таблицы при запуске)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
