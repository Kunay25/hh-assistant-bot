import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

def load_env() -> None:
    """
    Загрузка переменных окружения из .env файла в соответствии с требованиями практики.
    """
    load_dotenv(dotenv_path=ENV_PATH)

# Вызываем загрузку переменных окружения
load_env()

# Строка подключения к PostgreSQL задаётся только в локальном .env файле.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Токены ботов
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "").strip()
MAX_API_URL = os.getenv("MAX_API_URL", "https://platform-api.max.ru").rstrip("/")

# Настройки Yandex Cloud
YC_API_KEY = os.getenv("YC_API_KEY", "").strip()
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID", "").strip()
YC_ASSISTANT_ID = os.getenv("YC_ASSISTANT_ID", "").strip()
YC_MODEL = os.getenv("YC_MODEL", "yandexgpt").strip()

# Пути к данным
DOCS_DIR = BASE_DIR / os.getenv("DOCS_DIR", "data/hh_docs")

# Настройки репозитория документации HH.ru
HH_REPO_OWNER = "hhru"
HH_REPO_NAME = "api"
HH_DOCS_BRANCH = "master"
HH_API_GITHUB_CONTENTS_URL = f"https://api.github.com/repos/{HH_REPO_OWNER}/{HH_REPO_NAME}/contents/docs"
