"""Настройка логирования."""
import logging
import sys
from pathlib import Path
from config import LOG_LEVEL


def setup_logger(name: str = "telegram_bot") -> logging.Logger:
    """Настраивает и возвращает логгер."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    
    # Если у логгера уже есть handlers, не добавляем повторно
    if logger.handlers:
        return logger
    
    # Формат логов
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Консольный handler для обычных сообщений (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Handler для ошибок (stderr)
    error_handler = logging.StreamHandler(sys.stderr)
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    logger.addHandler(error_handler)
    
    # Файловый handler (опционально, если нужен)
    # log_file = Path("logs/bot.log")
    # log_file.parent.mkdir(parents=True, exist_ok=True)
    # file_handler = logging.FileHandler(log_file, encoding="utf-8")
    # file_handler.setFormatter(formatter)
    # logger.addHandler(file_handler)
    
    return logger

