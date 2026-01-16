"""Обработчик аудио."""
import os
import tempfile
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy import select
from services.api_service import send_to_claude, transcribe_audio
from database.models import async_session_maker, User, Message as DBMessage, Response
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = Router()

# Проверяем наличие ffmpeg-python
try:
    import ffmpeg
    FFMPEG_AVAILABLE = True
except ImportError:
    FFMPEG_AVAILABLE = False
    logger.warning("ffmpeg-python не установлен. Конвертация аудио будет недоступна.")


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


def _convert_audio_to_mp3(input_path: str, output_path: str) -> bool:
    """
    Конвертирует аудио файл в MP3 используя ffmpeg-python или subprocess.
    
    Args:
        input_path: Путь к исходному файлу
        output_path: Путь к выходному MP3 файлу
    
    Returns:
        True если конвертация успешна, False в противном случае
    """
    try:
        if FFMPEG_AVAILABLE:
            # Используем ffmpeg-python API
            import ffmpeg
            
            stream = ffmpeg.input(input_path)
            stream = ffmpeg.output(
                stream,
                output_path,
                acodec='libmp3lame',
                ar=16000,  # Частота дискретизации для Whisper
                ac=1,  # Моно
                **{'y': None}  # Перезаписать выходной файл
            )
            ffmpeg.run(stream, overwrite_output=True, quiet=True)
            
            logger.info(f"Аудио конвертировано через ffmpeg-python: {input_path} -> {output_path}")
            return True
        else:
            # Fallback на subprocess
            import subprocess
            
            cmd = [
                'ffmpeg',
                '-i', input_path,
                '-acodec', 'libmp3lame',
                '-ar', '16000',  # Частота дискретизации для Whisper
                '-ac', '1',  # Моно
                '-y',  # Перезаписать выходной файл
                output_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                logger.info(f"Аудио конвертировано через subprocess: {input_path} -> {output_path}")
                return True
            else:
                logger.error(f"Ошибка конвертации аудио: {result.stderr}")
                return False
                
    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут при конвертации аудио: {input_path}")
        return False
    except FileNotFoundError:
        logger.warning("ffmpeg не найден в системе, пропускаем конвертацию")
        return False
    except Exception as e:
        logger.error(f"Ошибка при конвертации аудио: {e}", exc_info=True)
        return False


@router.message(F.audio | F.voice)
async def handle_audio(message: Message):
    """Обработчик аудио и голосовых сообщений."""
    temp_file_path = None
    converted_file_path = None
    try:
        user_id = message.from_user.id
        username = message.from_user.username
        
        logger.info(f"Получено аудио от {user_id}")
        
        # Создаем/обновляем пользователя в БД
        await _ensure_user(user_id, username)
        
        # Отправляем индикатор обработки
        await message.bot.send_chat_action(message.chat.id, "record_voice")
        
        # Определяем тип аудио (voice или audio)
        if message.voice:
            audio_file = message.voice
            original_extension = "ogg"
        elif message.audio:
            audio_file = message.audio
            # Определяем расширение файла
            if audio_file.file_name:
                original_extension = audio_file.file_name.split(".")[-1].lower()
            else:
                original_extension = "ogg"
        else:
            await message.answer("Не удалось определить тип аудио файла.")
            logger.warning(f"Не удалось определить тип аудио от пользователя {user_id}")
            return
        
        # Проверяем размер файла
        if audio_file.file_size and audio_file.file_size > 20 * 1024 * 1024:  # 20MB
            await message.answer("Файл слишком большой. Максимальный размер: 20MB")
            logger.warning(f"Аудио слишком большое: {audio_file.file_size} байт от пользователя {user_id}")
            return
        
        # Скачиваем аудио на диск
        file = await message.bot.get_file(audio_file.file_id)
        
        # Создаем временный файл с оригинальным расширением
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{original_extension}") as tmp_file:
            temp_file_path = tmp_file.name
        
        # Скачиваем файл
        await message.bot.download_file(file.file_path, destination=temp_file_path)
        logger.info(f"Аудио скачано: {temp_file_path}, размер: {os.path.getsize(temp_file_path)} байт")
        
        # Конвертируем в MP3/WAV если нужно
        audio_file_path = temp_file_path
        needs_conversion = original_extension not in ['mp3', 'wav', 'm4a']
        
        if needs_conversion and FFMPEG_AVAILABLE:
            # Создаем путь для конвертированного файла
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_mp3:
                converted_file_path = tmp_mp3.name
            
            # Конвертируем
            if _convert_audio_to_mp3(temp_file_path, converted_file_path):
                audio_file_path = converted_file_path
                logger.info(f"Аудио конвертировано в MP3: {converted_file_path}")
            else:
                logger.warning(f"Не удалось конвертировать аудио, используем оригинал: {temp_file_path}")
        
        # Отправляем индикатор печати
        await message.bot.send_chat_action(message.chat.id, "typing")
        
        # Транскрибируем аудио через api_service.transcribe_audio()
        transcribed_text = await transcribe_audio(audio_file_path)
        
        if not transcribed_text:
            await message.answer(
                "Не удалось транскрибировать аудио. "
                "Попробуйте записать более четко или использовать другой формат."
            )
            logger.warning(f"Не удалось транскрибировать аудио пользователя {user_id}")
            return
        
        logger.info(f"Аудио транскрибировано: {len(transcribed_text)} символов")
        
        # Отправляем индикатор печати
        await message.bot.send_chat_action(message.chat.id, "typing")
        
        # Отправляем транскрибированный текст в Claude
        response_text = await send_to_claude(transcribed_text, user_id)
        
        if response_text:
            # Отправляем ответ пользователю
            await message.answer(response_text)
            logger.info(f"Ответ отправлен пользователю {user_id}")
            
            # Сохраняем в БД с message_type="audio"
            # send_to_claude уже сохраняет с message_type="text", поэтому нам нужно сохранить отдельно
            # с правильным типом
            await _save_message_and_response(
                user_id=user_id,
                content=transcribed_text,
                message_type="audio",
                response_text=response_text,
                model="claude-3-sonnet"  # Модель из send_to_claude
            )
        else:
            error_msg = "Извините, не удалось получить ответ от AI. Попробуйте позже."
            await message.answer(error_msg)
            logger.warning(f"Не удалось получить ответ от Claude для пользователя {user_id}")
            
    except Exception as e:
        logger.error(f"Ошибка обработки аудио: {e}", exc_info=True)
        try:
            await message.answer("Произошла ошибка при обработке аудио.")
        except Exception as send_error:
            logger.error(f"Не удалось отправить сообщение об ошибке: {send_error}", exc_info=True)
    finally:
        # Удаляем временные файлы
        for file_path in [temp_file_path, converted_file_path]:
            if file_path and os.path.exists(file_path):
                try:
                    os.unlink(file_path)
                    logger.debug(f"Временный файл удален: {file_path}")
                except Exception as e:
                    logger.warning(f"Не удалось удалить временный файл {file_path}: {e}")
