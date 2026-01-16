"""Сервис для извлечения текста из изображений с помощью EasyOCR."""
import asyncio
import os
from pathlib import Path
from typing import Optional
from PIL import Image
import easyocr
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Глобальная переменная для кэширования модели EasyOCR
_ocr_reader: Optional[easyocr.Reader] = None
_ocr_lock = asyncio.Lock()  # Блокировка для безопасной инициализации

# Константы
MAX_IMAGE_SIZE_MB = 2
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024
OCR_TIMEOUT = 30  # секунды
SUPPORTED_LANGUAGES = ['ru', 'en']  # Русский и английский


async def _initialize_ocr_reader() -> Optional[easyocr.Reader]:
    """
    Инициализирует EasyOCR reader один раз и кэширует его.
    
    Returns:
        EasyOCR Reader или None в случае ошибки
    """
    global _ocr_reader
    
    if _ocr_reader is not None:
        return _ocr_reader
    
    async with _ocr_lock:
        # Проверяем еще раз после получения блокировки
        if _ocr_reader is not None:
            return _ocr_reader
        
        try:
            logger.info("Инициализация EasyOCR reader (первый запуск, может занять время)...")
            # Запускаем в executor, так как EasyOCR инициализация блокирующая
            loop = asyncio.get_event_loop()
            _ocr_reader = await loop.run_in_executor(
                None,
                lambda: easyocr.Reader(SUPPORTED_LANGUAGES, gpu=False)
            )
            logger.info("EasyOCR reader успешно инициализирован")
            return _ocr_reader
        except Exception as e:
            logger.error(f"Ошибка при инициализации EasyOCR: {e}", exc_info=True)
            return None


def _compress_image_if_needed(image_path: str) -> Optional[str]:
    """
    Сжимает изображение, если его размер больше MAX_IMAGE_SIZE_MB.
    
    Args:
        image_path: Путь к исходному изображению
    
    Returns:
        Путь к сжатому изображению (или исходному, если сжатие не нужно),
        или None в случае ошибки
    """
    try:
        file_size = os.path.getsize(image_path)
        
        if file_size <= MAX_IMAGE_SIZE_BYTES:
            logger.debug(f"Изображение {image_path} не требует сжатия ({file_size / 1024 / 1024:.2f} MB)")
            return image_path
        
        logger.info(f"Сжатие изображения {image_path} ({file_size / 1024 / 1024:.2f} MB > {MAX_IMAGE_SIZE_MB} MB)")
        
        # Открываем изображение
        with Image.open(image_path) as img:
            # Конвертируем в RGB, если нужно
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Вычисляем коэффициент сжатия
            # Целевой размер: немного меньше MAX_IMAGE_SIZE_MB для запаса
            target_size_bytes = MAX_IMAGE_SIZE_BYTES * 0.9
            quality = 85  # Начальное качество
            
            # Создаем временный файл для сжатого изображения
            temp_path = image_path + ".compressed.jpg"
            
            # Пробуем разные уровни качества, пока не достигнем нужного размера
            for attempt in range(5):
                img.save(temp_path, 'JPEG', quality=quality, optimize=True)
                compressed_size = os.path.getsize(temp_path)
                
                if compressed_size <= target_size_bytes:
                    logger.info(
                        f"Изображение сжато: {file_size / 1024 / 1024:.2f} MB -> "
                        f"{compressed_size / 1024 / 1024:.2f} MB (качество: {quality})"
                    )
                    return temp_path
                
                # Уменьшаем качество для следующей попытки
                quality = max(30, quality - 15)
            
            # Если не удалось сжать достаточно, все равно возвращаем сжатое
            compressed_size = os.path.getsize(temp_path)
            logger.warning(
                f"Не удалось сжать изображение до {MAX_IMAGE_SIZE_MB} MB: "
                f"{compressed_size / 1024 / 1024:.2f} MB (качество: {quality})"
            )
            return temp_path
            
    except Exception as e:
        logger.error(f"Ошибка при сжатии изображения {image_path}: {e}", exc_info=True)
        return image_path  # Возвращаем исходный путь в случае ошибки


async def extract_text_from_photo(image_path: str) -> str:
    """
    Извлекает текст из изображения с помощью EasyOCR.
    
    Оптимизации для слабого сервера:
    - Кэширует модель EasyOCR в памяти (инициализирует один раз)
    - Сжимает изображение перед OCR, если оно больше 2MB
    - Таймаут на обработку 30 секунд
    
    Args:
        image_path: Путь к изображению
    
    Returns:
        Извлечённый текст или пустая строка, если текст не найден или произошла ошибка
    """
    compressed_path: Optional[str] = None
    
    try:
        # Проверяем существование файла
        if not os.path.exists(image_path):
            logger.error(f"Файл изображения не найден: {image_path}")
            return ""
        
        # Инициализируем OCR reader (кэшируется)
        reader = await _initialize_ocr_reader()
        if reader is None:
            logger.error("Не удалось инициализировать EasyOCR reader")
            return ""
        
        # Сжимаем изображение, если нужно
        processed_image_path = _compress_image_if_needed(image_path)
        if processed_image_path is None:
            logger.error(f"Не удалось обработать изображение: {image_path}")
            return ""
        
        # Запоминаем путь к сжатому файлу для последующего удаления
        if processed_image_path != image_path:
            compressed_path = processed_image_path
        
        logger.info(f"Начало OCR обработки: {processed_image_path}")
        
        # Запускаем OCR с таймаутом
        loop = asyncio.get_event_loop()
        
        try:
            # Выполняем OCR в executor с таймаутом
            results = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: reader.readtext(processed_image_path)
                ),
                timeout=OCR_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.error(f"Таймаут при обработке OCR (>{OCR_TIMEOUT} секунд): {image_path}")
            return ""
        
        # Извлекаем текст из результатов
        if not results:
            logger.info(f"Текст не найден на изображении: {image_path}")
            return ""
        
        # Объединяем все найденные тексты
        extracted_texts = [result[1] for result in results if len(result) > 1]
        full_text = "\n".join(extracted_texts)
        
        if full_text:
            logger.info(f"Текст успешно извлечен: {len(full_text)} символов из {image_path}")
        else:
            logger.info(f"Текст не найден на изображении: {image_path}")
        
        return full_text
        
    except asyncio.TimeoutError:
        logger.error(f"Таймаут при обработке OCR: {image_path}")
        return ""
    except Exception as e:
        logger.error(f"Ошибка при извлечении текста из изображения {image_path}: {e}", exc_info=True)
        return ""
    finally:
        # Удаляем временный сжатый файл, если он был создан
        if compressed_path and os.path.exists(compressed_path) and compressed_path != image_path:
            try:
                os.unlink(compressed_path)
                logger.debug(f"Временный файл удален: {compressed_path}")
            except Exception as e:
                logger.warning(f"Не удалось удалить временный файл {compressed_path}: {e}")
