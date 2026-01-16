"""Сервис для транскрибирования аудио."""
from typing import Optional
from services.api_service import APIService
from utils.logger import setup_logger

logger = setup_logger(__name__)


class TranscriptionService:
    """Сервис для транскрибирования аудио через Whisper API."""
    
    def __init__(self):
        self.api_service = APIService()
    
    async def transcribe_audio_bytes(
        self,
        audio_bytes: bytes,
        filename: str = "audio.ogg",
        language: Optional[str] = None
    ) -> Optional[str]:
        """
        Транскрибирует аудио из байтов.
        
        Args:
            audio_bytes: Байты аудио файла
            filename: Имя файла
            language: Код языка (опционально)
        
        Returns:
            Транскрибированный текст или None
        """
        try:
            text = await self.api_service.transcribe_audio_from_bytes(
                audio_bytes,
                filename,
                language
            )
            return text
        except Exception as e:
            logger.error(f"Ошибка транскрибирования: {e}", exc_info=True)
            return None

