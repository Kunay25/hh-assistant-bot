# HH Assistant Bot — помощник по API HeadHunter

Учебный Python-проект: бот для MAX, Telegram и консоли. Помогает разработчикам находить ответы по официальной документации API HH.ru. Проект предназначен для демонстрации на практике.

## 1. Возможности

- Отвечает на вопросы про OAuth, вакансии, резюме и другие методы API HeadHunter.
- Скачивает Markdown-документацию из [hhru/api](https://github.com/hhru/api) и ищет подходящие разделы.
- Может обращаться к YandexGPT с найденным контекстом (если настроен Yandex Cloud); без ключей показывает релевантные фрагменты документации.
- Поддерживает MAX, Telegram и локальный CLI-режим.
- Хранит пользователей и историю вопросов в **PostgreSQL через SQLAlchemy ORM**.

## 2. Структура проекта

```text
hh_assistant_bot/
├── .env.example       # Образец переменных окружения, без настоящих секретов
├── .gitignore         # Файлы, которые нельзя или не нужно публиковать
├── requirements.txt   # Python-зависимости
├── README.md          # Описание, установка и демонстрация
├── config.py          # Загрузка .env с помощью load_env() / python-dotenv
├── database.py        # PostgreSQL, SQLAlchemy ORM, модели users/interactions
├── knowledge_base.py  # Загрузка и поиск по документации HH.ru
├── yandex_assistant.py# Ответы YandexGPT или локальный резервный ответ
├── bot_max.py         # Бот MAX
├── bot_telegram.py    # Бот Telegram
├── main.py            # Точка входа
└── test_system.py     # Проверка основных компонентов
```

Папки `data/` и `.venv/`, а также локальный `.env` создаются у разработчика и не публикуются на GitHub.

## 3. Требования

- Windows 10/11, Python **3.10 или выше** (рекомендуется 3.12)
- PostgreSQL (вместе с pgAdmin), работающий локально
- Доступ к сети для скачивания документации HH.ru
- Токен Telegram/MAX — только если нужен запуск в соответствующем мессенджере

## 4. Установка — пошагово

**1.** Скачать и установить PostgreSQL вместе с pgAdmin: https://www.postgresql.org/download/windows/. Во время установки записать пароль пользователя `postgres`, оставить порт `5432` (если не занят).

**2.** Открыть pgAdmin, подключиться к локальному серверу, в разделе **Databases** выбрать **Create → Database...**, указать имя `hh_bot_db` и сохранить. *pgAdmin — это интерфейс; сервер PostgreSQL устанавливается отдельно или вместе с установщиком PostgreSQL.*

**3.** Открыть проект в VS Code и создать виртуальное окружение в PowerShell (необязательно, но рекомендуется):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Если предпочитаете установленный глобально Python:

```powershell
python -m pip install -r requirements.txt
```

**4.** Скопировать `.env.example` в `.env` (PowerShell):

```powershell
Copy-Item .env.example .env
```

В файле `.env` установить **реальный** пароль вместо `postgres_here` в `DATABASE_URL`, при необходимости заполнить токены Telegram/MAX и ключи Yandex Cloud. Не добавлять `.env` в Git.

**5.** Проверить БД и запустить консольную версию:

```powershell
.\.venv\Scripts\python.exe main.py --cli
```

При первом запуске SQLAlchemy создаёт в существующей БД `hh_bot_db` таблицы `users` и `interactions`. Если PostgreSQL не запущен, имя БД или пароль неправильные, подключение завершится ошибкой.

**6.** Для запуска Telegram (после заполнения `TELEGRAM_BOT_TOKEN`):

```powershell
.\.venv\Scripts\python.exe main.py --telegram
```

Для MAX: `python main.py --max`; для всех настроенных ботов: `python main.py --all`.

**7.** Обновление документации и тесты:

```powershell
.\.venv\Scripts\python.exe main.py --update-kb
.\.venv\Scripts\python.exe test_system.py
```

**Внимание:** `test_system.py` создаёт тестовые записи в настроенной базе данных. Желательно выполнять его в отдельной тестовой БД.

## 5. Переменные окружения (.env)

Все секреты читаются из локального `.env` функцией `load_env()` в файле `config.py` при помощи `load_dotenv()` из библиотеки `python-dotenv`. В GitHub публикуется **только** `.env.example` с безопасными примерными значениями.

| Поле | Что означает | Пример заполнения |
|---|---|---|
| `DATABASE_URL` | Строка подключения к PostgreSQL: логин, пароль, адрес, порт и БД | `postgresql+psycopg2://postgres:postgres_here@localhost:5432/hh_bot_db` |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота от @BotFather | `123456:EXAMPLE_ONLY` |
| `MAX_BOT_TOKEN` | Токен бота в мессенджере MAX | `example_max_token` |
| `MAX_API_URL` | Адрес MAX Bot API | `https://platform-api.max.ru` |
| `YC_API_KEY` | Секретный API-ключ Yandex Cloud | `example_api_key` |
| `YC_FOLDER_ID` | Идентификатор каталога Yandex Cloud | `example_folder_id` |
| `YC_ASSISTANT_ID` | Идентификатор Yandex Assistant, если создан | `example_assistant_id` |
| `YC_MODEL` | Имя модели YandexGPT | `yandexgpt` |
| `DOCS_DIR` | Папка для загруженной документации HH.ru | `data/hh_docs` |

Значения с префиксом `example` и паролем `postgres_here` — **заглушки**, не настоящие ключи. Поля токенов и Yandex Cloud можно оставить пустыми для CLI. `DATABASE_URL` обязателен. Если пароль для PostgreSQL содержит символы `@`, `:`, `/` или `%`, их нужно URL-кодировать.

## 6. Устройство базы данных

`database.py` использует SQLAlchemy ORM и драйвер `psycopg2-binary` для PostgreSQL. Таблицы:

- `users`: `id` (PK), `platform`, `platform_user_id`, `username`, `first_name`, `created_at`, `last_seen`; пара (`platform`, `platform_user_id`) уникальна.
- `interactions`: `id` (PK), `user_id` (FK → `users.id`), `user_query`, `bot_response`, `sources`, `created_at`.

Основные операции: регистрация или обновление пользователя, сохранение вопросов/ответов, история последних сообщений, статистика по платформам.

## 7. Проверка перед публикацией

Выполнить в PowerShell из папки проекта:

```powershell
git status --short
git check-ignore .env
```

Вторая команда должна показать `.env`, то есть секретный файл игнорируется. Проверьте, чтобы в `git status` не было `.env`, локальных БД, `.venv` и токенов.

## 8. Публикация на GitHub

Создать пустой **Public** репозиторий `hh-assistant-bot` на GitHub **без** автоматической генерации README (он уже есть в проекте). Из папки проекта выполнить:

```powershell
git init
git branch -M main
git add .
git status
git commit -m "Initial commit: HH assistant bot"
git remote add origin https://github.com/ВАШ_ЛОГИН/hh-assistant-bot.git
git push -u origin main
```