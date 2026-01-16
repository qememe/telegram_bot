"""Сервис для работы с proxyapi."""
import aiohttp
import asyncio
from typing import Optional
from datetime import datetime, timezone
from config import PROXYAPI_URL, PROXYAPI_KEY
from utils.logger import setup_logger
from database.models import async_session_maker, Message, Response

logger = setup_logger(__name__)

# Константы для retry логики
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # секунды
TIMEOUT = 30.0  # секунды


async def _make_request_with_retry(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    headers: dict,
    **kwargs
) -> Optional[aiohttp.ClientResponse]:
    """
    Выполняет HTTP запрос с retry логикой.
    
    Args:
        session: aiohttp сессия
        method: HTTP метод (GET, POST, etc.)
        url: URL для запроса
        headers: Заголовки запроса
        **kwargs: Дополнительные параметры для запроса
    
    Returns:
        Response объект или None в случае ошибки
    """
    last_exception = None
    
    for attempt in range(MAX_RETRIES):
        try:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT)
            async with session.request(
                method,
                url,
                headers=headers,
                timeout=timeout,
                **kwargs
            ) as response:
                # Если успешный ответ или ошибка, которую не нужно retry
                if response.status == 200:
                    return response
                elif response.status == 429:  # Too Many Requests
                    if attempt < MAX_RETRIES - 1:
                        wait_time = RETRY_DELAY * (2 ** attempt)  # Exponential backoff
                        logger.warning(
                            f"Получен 429, попытка {attempt + 1}/{MAX_RETRIES}, "
                            f"ожидание {wait_time}с"
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        error_text = await response.text()
                        logger.error(f"429 после {MAX_RETRIES} попыток: {error_text}")
                        return None
                elif response.status >= 500:  # Server errors
                    if attempt < MAX_RETRIES - 1:
                        wait_time = RETRY_DELAY * (2 ** attempt)
                        logger.warning(
                            f"Ошибка сервера {response.status}, "
                            f"попытка {attempt + 1}/{MAX_RETRIES}, ожидание {wait_time}с"
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        error_text = await response.text()
                        logger.error(f"Ошибка сервера {response.status} после {MAX_RETRIES} попыток: {error_text}")
                        return None
                else:
                    # Другие ошибки (4xx кроме 429) - не retry
                    error_text = await response.text()
                    logger.error(f"Ошибка {response.status}: {error_text}")
                    return None
                    
        except asyncio.TimeoutError:
            last_exception = "Timeout"
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    f"Timeout, попытка {attempt + 1}/{MAX_RETRIES}, ожидание {wait_time}с"
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"Timeout после {MAX_RETRIES} попыток")
                return None
        except aiohttp.ClientError as e:
            last_exception = str(e)
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    f"Ошибка клиента: {e}, попытка {attempt + 1}/{MAX_RETRIES}, "
                    f"ожидание {wait_time}с"
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"Ошибка клиента после {MAX_RETRIES} попыток: {e}", exc_info=True)
                return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка при запросе: {e}", exc_info=True)
            return None
    
    return None


async def send_to_claude(text: str, user_id: int) -> str:
    """
    Отправляет текст в Claude через proxyapi.
    
    Args:
        text: Текст для отправки
        user_id: ID пользователя
    
    Returns:
        Ответ от Claude или пустая строка в случае ошибки
    """
    model = "claude-3-sonnet"
    max_tokens = 1024
    
    logger.info(f"Отправка запроса в Claude для пользователя {user_id}: {len(text)} символов")
    
    url = f"{PROXYAPI_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {PROXYAPI_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": text
            }
        ],
        "max_tokens": max_tokens
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            response = await _make_request_with_retry(
                session,
                "POST",
                url,
                headers,
                json=payload
            )
            
            if response is None:
                logger.error("Не удалось получить ответ от Claude после всех попыток")
                return ""
            
            result = await response.json()
            
            # Парсим ответ OpenAI-совместимого формата
            choices = result.get("choices", [])
            if not choices:
                logger.warning("Пустой ответ от Claude (нет choices)")
                return ""
            
            message = choices[0].get("message", {})
            bot_response = message.get("content", "")
            
            if not bot_response:
                logger.warning("Пустой ответ от Claude (нет content)")
                return ""
            
            # Получаем информацию об использовании токенов
            usage = result.get("usage", {})
            tokens_used = usage.get("total_tokens")
            
            logger.info(
                f"Получен ответ от Claude для пользователя {user_id}: "
                f"{len(bot_response)} символов, токенов: {tokens_used}"
            )
            
            # Логируем в БД
            await _log_request_to_db(user_id, text, bot_response, model, tokens_used)
            
            return bot_response
            
    except Exception as e:
        logger.error(f"Ошибка при отправке запроса в Claude: {e}", exc_info=True)
        return ""


async def transcribe_audio(audio_file_path: str) -> str:
    """
    Отправляет аудио на Whisper через proxyapi.
    
    Args:
        audio_file_path: Путь к аудио файлу
    
    Returns:
        Транскрибированный текст или пустая строка в случае ошибки
    """
    logger.info(f"Транскрибирование аудио: {audio_file_path}")
    
    url = f"{PROXYAPI_URL}/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {PROXYAPI_KEY}"
    }
    
    try:
        # Читаем аудио файл
        with open(audio_file_path, "rb") as f:
            audio_bytes = f.read()
        
        filename = audio_file_path.split("/")[-1]
        
        # Определяем content type по расширению
        content_type = "audio/mpeg"
        if filename.endswith(".ogg") or filename.endswith(".oga"):
            content_type = "audio/ogg"
        elif filename.endswith(".wav"):
            content_type = "audio/wav"
        elif filename.endswith(".m4a"):
            content_type = "audio/m4a"
        
        data = aiohttp.FormData()
        data.add_field(
            "file",
            audio_bytes,
            filename=filename,
            content_type=content_type
        )
        data.add_field("model", "whisper-1")
        
        async with aiohttp.ClientSession() as session:
            response = await _make_request_with_retry(
                session,
                "POST",
                url,
                headers,
                data=data
            )
            
            if response is None:
                logger.error("Не удалось транскрибировать аудио после всех попыток")
                return ""
            
            result = await response.json()
            transcribed_text = result.get("text", "")
            
            if transcribed_text:
                logger.info(f"Аудио транскрибировано: {len(transcribed_text)} символов")
            else:
                logger.warning("Пустой результат транскрибирования")
            
            return transcribed_text
            
    except FileNotFoundError:
        logger.error(f"Аудио файл не найден: {audio_file_path}")
        return ""
    except Exception as e:
        logger.error(f"Ошибка при транскрибировании аудио: {e}", exc_info=True)
        return ""


async def get_available_models() -> list:
    """
    Проверяет доступные модели на proxyapi (для дебага).
    
    Returns:
        Список доступных моделей или пустой список в случае ошибки
    """
    logger.info("Запрос доступных моделей на proxyapi")
    
    url = f"{PROXYAPI_URL}/models"
    headers = {
        "Authorization": f"Bearer {PROXYAPI_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            response = await _make_request_with_retry(
                session,
                "GET",
                url,
                headers
            )
            
            if response is None:
                logger.error("Не удалось получить список моделей после всех попыток")
                return []
            
            result = await response.json()
            models_data = result.get("data", [])
            
            models = [model.get("id", "") for model in models_data if model.get("id")]
            
            logger.info(f"Получено {len(models)} доступных моделей")
            if models:
                logger.debug(f"Модели: {', '.join(models)}")
            
            return models
            
    except Exception as e:
        logger.error(f"Ошибка при получении списка моделей: {e}", exc_info=True)
        return []


async def _log_request_to_db(
    user_id: int,
    request_text: str,
    response_text: str,
    model: str,
    tokens_used: Optional[int] = None
) -> None:
    """
    Логирует запрос и ответ в БД.
    
    Args:
        user_id: ID пользователя Telegram
        request_text: Текст запроса
        response_text: Текст ответа
        model: Использованная модель
        tokens_used: Количество использованных токенов
    """
    try:
        async with async_session_maker() as session:
            # Создаем или находим сообщение пользователя
            # Для простоты создаем новое сообщение для каждого запроса
            message = Message(
                user_id=user_id,
                content=request_text,
                message_type="text",
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
                f"Запрос и ответ записаны в БД: message_id={message.id}, "
                f"response_id={response.id}"
            )
            
    except Exception as e:
        logger.error(f"Ошибка при записи в БД: {e}", exc_info=True)
        # Не пробрасываем исключение, чтобы не прерывать основной поток


class APIService:
    """
    Класс-обертка для обратной совместимости.
    Использует новые функции внутри.
    """
    
    async def get_chat_response(
        self,
        text: str,
        user_id: Optional[int] = None
    ) -> Optional[str]:
        """
        Получает ответ от AI через Claude (для обратной совместимости).
        
        Args:
            text: Текст запроса
            user_id: ID пользователя
        
        Returns:
            Ответ от AI или None в случае ошибки
        """
        if user_id is None:
            logger.warning("user_id не указан, используется 0")
            user_id = 0
        
        response = await send_to_claude(text, user_id)
        return response if response else None
    
    async def transcribe_audio_from_bytes(
        self,
        audio_bytes: bytes,
        filename: str = "audio.ogg",
        language: Optional[str] = None
    ) -> Optional[str]:
        """
        Транскрибирует аудио из байтов (для обратной совместимости).
        
        Args:
            audio_bytes: Байты аудио файла
            filename: Имя файла
            language: Код языка (опционально, пока не используется)
        
        Returns:
            Транскрибированный текст или None
        """
        import tempfile
        import os
        
        try:
            # Создаем временный файл
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{filename.split('.')[-1]}") as tmp_file:
                tmp_file.write(audio_bytes)
                tmp_path = tmp_file.name
            
            try:
                # Транскрибируем через функцию
                result = await transcribe_audio(tmp_path)
                return result if result else None
            finally:
                # Удаляем временный файл
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                    
        except Exception as e:
            logger.error(f"Ошибка при транскрибировании из байтов: {e}", exc_info=True)
            return None
