"""Конфигурация бота и переменные окружения."""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

# ProxyAPI настройки
PROXYAPI_URL = "https://api.proxyapi.ru/openai/v1"
PROXYAPI_KEY = os.getenv("PROXYAPI_KEY", "")

# База данных
# Конвертируем sqlite:/// в sqlite+aiosqlite:/// для async поддержки
_db_url = os.getenv("DATABASE_URL", "sqlite:///bot_data.db")
if _db_url.startswith("sqlite:///") and "+aiosqlite" not in _db_url:
    DATABASE_URL = _db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
else:
    DATABASE_URL = _db_url

# Логирование
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
