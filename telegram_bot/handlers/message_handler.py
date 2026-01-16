"""Обработчик текстовых сообщений."""
import os
import tempfile
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select
from services.api_service import send_to_claude
from database.models import async_session_maker, User, Message as DBMessage, Response
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = Router()


async def _ensure_user(telegram_id: int, username: str | None = None) -> None:
    """
    Создает или обновляет пользователя в БД.
    
    Args:
        telegram_id: ID пользователя в Telegram
        username: Имя пользователя (опционально)
    """
    try:
        async with async_session_maker() as session:
            # Проверяем, существует ли пользователь
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            
            now = datetime.now(timezone.utc)
            
            if user is None:
                # Создаем нового пользователя
                user = User(
                    telegram_id=telegram_id,
                    username=username,
                    first_message_date=now,
                    last_message_date=now
                )
                session.add(user)
                logger.info(f"Создан новый пользователь: {telegram_id}")
            else:
                # Обновляем последнее сообщение
                user.last_message_date = now
                if username and user.username != username:
                    user.username = username
                logger.debug(f"Обновлен пользователь: {telegram_id}")
            
            await session.commit()
    except Exception as e:
        logger.error(f"Ошибка при создании/обновлении пользователя {telegram_id}: {e}", exc_info=True)


async def _save_message_and_response(
    user_id: int,
    content: str,
    message_type: str,
    response_text: str,
    model: str | None = None,
    tokens_used: int | None = None
) -> None:
    """
    Сохраняет сообщение и ответ в БД.
    
    Args:
        user_id: ID пользователя Telegram
        content: Содержимое сообщения
        message_type: Тип сообщения (text, photo, audio)
        response_text: Текст ответа бота
        model: Использованная модель (опционально)
        tokens_used: Количество использованных токенов (опционально)
    """
    try:
        async with async_session_maker() as session:
            # Создаем сообщение
            message = DBMessage(
                user_id=user_id,
                content=content,
                message_type=message_type,
                created_at=datetime.now(timezone.utc)
            )
            session.add(message)
            await session.flush()  # Получаем message.id
            
            # Создаем ответ
            response = Response(
                message_id=message.id,
                bot_response=response_text,
                model_used=model,
                tokens_used=tokens_used,
                created_at=datetime.now(timezone.utc)
            )
            session.add(response)
            
            await session.commit()
            logger.debug(
                f"Сообщение и ответ сохранены в БД: message_id={message.id}, "
                f"response_id={response.id}, type={message_type}"
            )
    except Exception as e:
        logger.error(f"Ошибка при сохранении в БД: {e}", exc_info=True)
        # Не пробрасываем исключение, чтобы не прерывать основной поток


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start."""
    try:
        await message.answer(
            "Привет! Я бот для обработки сообщений, фото и аудио.\n\n"
            "Отправь мне:\n"
            "- Текстовое сообщение для получения ответа от AI\n"
            "- Фото для извлечения текста через OCR\n"
            "- Аудио для транскрибирования и получения ответа"
        )
        logger.info(f"Команда /start от пользователя {message.from_user.id}")
    except Exception as e:
        logger.error(f"Ошибка обработки команды /start: {e}", exc_info=True)
        await message.answer("Произошла ошибка при обработке команды.")


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help."""
    try:
        await message.answer(
            "Доступные команды:\n"
            "/start - Начать работу с ботом\n"
            "/help - Показать эту справку\n\n"
            "Возможности:\n"
            "• Текстовые сообщения - получай ответы от AI\n"
            "• Фото - извлечение текста через OCR\n"
            "• Аудио - транскрибирование и ответ от AI"
        )
        logger.info(f"Команда /help от пользователя {message.from_user.id}")
    except Exception as e:
        logger.error(f"Ошибка обработки команды /help: {e}", exc_info=True)
        await message.answer("Произошла ошибка при обработке команды.")


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message):
    """Обработчик текстовых сообщений."""
    try:
        user_text = message.text
        user_id = message.from_user.id
        username = message.from_user.username
        
        logger.info(f"Получено текстовое сообщение от {user_id}: {len(user_text)} символов")
        
        # Создаем/обновляем пользователя в БД
        await _ensure_user(user_id, username)
        
        # Отправляем индикатор печати
        await message.bot.send_chat_action(message.chat.id, "typing")
        
        # Отправляем в Claude через api_service
        response_text = await send_to_claude(user_text, user_id)
        
        if response_text:
            # Отправляем ответ пользователю
            await message.answer(response_text)
            logger.info(f"Ответ отправлен пользователю {user_id}")
            
            # Сохраняем в БД (send_to_claude уже сохраняет, но мы сохраняем еще раз для явности)
            # На самом деле send_to_claude уже сохраняет через _log_request_to_db,
            # но мы можем сохранить еще раз для консистентности или пропустить
            # Для простоты оставим как есть - send_to_claude уже сохраняет
        else:
            error_msg = "Извините, не удалось получить ответ. Попробуйте позже."
            await message.answer(error_msg)
            logger.warning(f"Не удалось получить ответ для пользователя {user_id}")
            
    except Exception as e:
        logger.error(f"Ошибка обработки текстового сообщения: {e}", exc_info=True)
        try:
            await message.answer("Произошла ошибка при обработке сообщения.")
        except Exception as send_error:
            logger.error(f"Не удалось отправить сообщение об ошибке: {send_error}", exc_info=True)
