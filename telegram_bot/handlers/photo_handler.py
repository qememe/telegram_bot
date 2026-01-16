"""Обработчик фото."""
import os
import tempfile
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy import select
from services.api_service import send_to_claude
from services.ocr_service import extract_text_from_photo
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


@router.message(F.photo)
async def handle_photo(message: Message):
    """Обработчик фото."""
    temp_file_path = None
    try:
        user_id = message.from_user.id
        username = message.from_user.username
        
        logger.info(f"Получено фото от {user_id}")
        
        # Создаем/обновляем пользователя в БД
        await _ensure_user(user_id, username)
        
        # Отправляем индикатор обработки
        await message.bot.send_chat_action(message.chat.id, "typing")
        
        # Получаем фото наибольшего размера
        photo = message.photo[-1]
        
        # Проверяем размер файла
        if photo.file_size and photo.file_size > 20 * 1024 * 1024:  # 20MB
            await message.answer("Файл слишком большой. Максимальный размер: 20MB")
            logger.warning(f"Фото слишком большое: {photo.file_size} байт от пользователя {user_id}")
            return
        
        # Скачиваем фото на диск
        file = await message.bot.get_file(photo.file_id)
        
        # Создаем временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
            temp_file_path = tmp_file.name
        
        # Скачиваем файл
        await message.bot.download_file(file.file_path, destination=temp_file_path)
        logger.info(f"Фото скачано: {temp_file_path}, размер: {os.path.getsize(temp_file_path)} байт")
        
        # Извлекаем текст через OCR
        extracted_text = await extract_text_from_photo(temp_file_path)
        
        if not extracted_text:
            await message.answer(
                "Не удалось извлечь текст из изображения. "
                "Убедитесь, что на фото есть читаемый текст."
            )
            logger.warning(f"Не удалось извлечь текст из фото пользователя {user_id}")
            return
        
        logger.info(f"Текст извлечен из фото: {len(extracted_text)} символов")
        
        # Отправляем индикатор печати
        await message.bot.send_chat_action(message.chat.id, "typing")
        
        # Отправляем извлеченный текст в Claude
        response_text = await send_to_claude(extracted_text, user_id)
        
        if response_text:
            # Отправляем ответ пользователю
            await message.answer(response_text)
            logger.info(f"Ответ отправлен пользователю {user_id}")
            
            # Сохраняем в БД с message_type="photo"
            # send_to_claude уже сохраняет с message_type="text", поэтому нам нужно сохранить отдельно
            # с правильным типом. Для этого мы сохраним сообщение с типом "photo" и ответом
            await _save_message_and_response(
                user_id=user_id,
                content=extracted_text,
                message_type="photo",
                response_text=response_text,
                model="claude-3-sonnet"  # Модель из send_to_claude
            )
        else:
            error_msg = "Извините, не удалось получить ответ от AI. Попробуйте позже."
            await message.answer(error_msg)
            logger.warning(f"Не удалось получить ответ от Claude для пользователя {user_id}")
            
    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}", exc_info=True)
        try:
            await message.answer("Произошла ошибка при обработке фото.")
        except Exception as send_error:
            logger.error(f"Не удалось отправить сообщение об ошибке: {send_error}", exc_info=True)
    finally:
        # Удаляем временный файл
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
                logger.debug(f"Временный файл удален: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Не удалось удалить временный файл {temp_file_path}: {e}")
