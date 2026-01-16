"""Точка входа для запуска Telegram бота."""
import asyncio
import signal
import sys
from typing import Any
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent

from config import TELEGRAM_TOKEN
from utils.logger import setup_logger
from handlers import message_handler, photo_handler, audio_handler
from database.models import init_db
from services.ocr_service import _initialize_ocr_reader

# Настройка логирования
logger = setup_logger(__name__)

# Глобальные переменные для graceful shutdown
bot: Bot | None = None
dp: Dispatcher | None = None


async def on_startup() -> None:
    """Выполняется при запуске бота."""
    logger.info("=" * 50)
    logger.info("Запуск Telegram бота...")
    logger.info("=" * 50)


async def on_shutdown() -> None:
    """Выполняется при остановке бота."""
    logger.info("=" * 50)
    logger.info("Остановка бота...")
    logger.info("=" * 50)
    
    if bot:
        await bot.session.close()
        logger.info("Сессия бота закрыта")


async def error_handler(event: ErrorEvent, exception: Exception) -> Any:
    """
    Глобальный обработчик ошибок на уровне dispatcher.
    
    Ловит необработанные исключения и отправляет сообщение пользователю.
    """
    logger.error(
        f"Необработанная ошибка в обработчике: {exception}",
        exc_info=True,
        extra={"update": event.update}
    )
    
    # Пытаемся отправить сообщение пользователю
    try:
        update = event.update
        if update.message:
            await update.message.answer("Ошибка обработки")
        elif update.callback_query:
            await update.callback_query.answer("Ошибка обработки", show_alert=True)
    except Exception as send_error:
        logger.error(f"Не удалось отправить сообщение об ошибке: {send_error}", exc_info=True)
    
    # Возвращаем True, чтобы исключение не пробрасывалось дальше
    return True


def setup_signal_handlers() -> None:
    """Настраивает обработчики сигналов для graceful shutdown."""
    def signal_handler(signum: int, frame: Any) -> None:
        logger.info(f"Получен сигнал {signum}, начинаем graceful shutdown...")
        # aiogram сам обработает остановку через KeyboardInterrupt
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


async def main() -> None:
    """Основная функция запуска бота."""
    global bot, dp
    
    try:
        # Логирование запуска
        logger.info("Инициализация бота...")
        
        # 1. Загрузка конфига из config.py
        if not TELEGRAM_TOKEN:
            logger.error("TELEGRAM_TOKEN не установлен! Проверьте .env файл.")
            sys.exit(1)
        logger.info("Конфигурация загружена")
        
        # 2. Инициализация БД
        logger.info("Инициализация базы данных...")
        await init_db()
        logger.info("База данных инициализирована")
        
        # 3. Инициализация EasyOCR модели (кэширование)
        logger.info("Инициализация EasyOCR модели (может занять время при первом запуске)...")
        ocr_reader = await _initialize_ocr_reader()
        if ocr_reader:
            logger.info("EasyOCR модель успешно инициализирована и закэширована")
        else:
            logger.warning("Не удалось инициализировать EasyOCR модель, OCR будет недоступен")
        
        # 4. Создание бота и диспетчера
        logger.info("Создание бота и диспетчера...")
        bot = Bot(
            token=TELEGRAM_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        dp = Dispatcher()
        
        # 5. Регистрация обработчиков
        
        # Команды /start и /help уже зарегистрированы в message_handler
        # Фильтры на текст, фото, аудио также уже зарегистрированы в соответствующих handlers
        
        # Подключение всех handlers из handlers/
        dp.include_router(message_handler.router)
        dp.include_router(photo_handler.router)
        dp.include_router(audio_handler.router)
        
        # Регистрация глобального обработчика ошибок
        dp.errors.register(error_handler)
        
        # Регистрация событий запуска/остановки
        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)
        
        logger.info("Все обработчики зарегистрированы")
        logger.info("Бот готов к работе")
        logger.info("=" * 50)
        
        # 6. Запуск polling
        await dp.start_polling(bot, handle_as_tasks=True)
        
    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания (Ctrl+C)")
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Graceful shutdown
        await on_shutdown()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    # Настройка обработчиков сигналов для graceful shutdown
    setup_signal_handlers()
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
