# HH Assistant Bot

Чат-бот для MAX, Telegram и консоли, который помогает разработчикам искать ответы по официальной документации API HH.ru (`github.com/hhru/api`).

## Что умеет бот

- Отвечает на вопросы по OAuth-авторизации, вакансиям, резюме, откликам и ошибкам API HeadHunter.
- Скачивает Markdown-документацию из `hhru/api` (папка `docs`), сверяет список файлов при обновлении, удаляет устаревшие документы и сохраняет версию и дату загрузки в `kb_metadata.json`.
- Ищет ответы гибридным поиском на базе локального Qdrant и FastEmbed: смысловые векторы (`paraphrase-multilingual-MiniLM-L12-v2`) плюс лексический поиск (`Qdrant/bm25` с русским стеммером) через алгоритм RRF.
- При заполненных ключах Yandex Cloud передаёт найденный контекст в YandexGPT; без ключей возвращает подходящий фрагмент документации и ссылки на статьи.
- Сохраняет пользователей и историю диалогов в PostgreSQL через SQLAlchemy ORM.
- Ограничивает доступ к служебным командам `/stats` и `/update_kb` по списку `ADMIN_IDS` из `.env`.

## Структура проекта

```text
hh_assistant_bot/
├── .env.example        # Шаблон настроек без реальных ключей
├── .gitignore          # Исключения для Git (.env, data/, кеш, сертификаты)
├── requirements.txt    # Зависимости проекта
├── README.md           # Описание и инструкция по запуску
├── config.py           # Загрузка .env через load_env() и проверка ADMIN_IDS
├── database.py         # Модели SQLAlchemy (User, Interaction) и работа с PostgreSQL
├── knowledge_base.py   # Синхронизация docs с GitHub и гибридный поиск в Qdrant
├── yandex_assistant.py # Подготовка контекста и запросы в YandexGPT
├── bot_max.py          # Обработка сообщений и polling для мессенджера MAX
├── bot_telegram.py     # Обработка команд и сообщений для Telegram
├── main.py             # Общий запуск (CLI / Telegram / MAX / --all)
└── test_system.py      # Автотесты основных модулей
```

Папки `data/`, `.venv/` и файл `.env` создаются локально и не загружаются на GitHub.

## Требования

- Windows 10/11, Python 3.10 или выше (проект проверялся на Python 3.12).
- Локальный сервер PostgreSQL и pgAdmin: https://www.postgresql.org/download/windows/
- Доступ в интернет для скачивания документации HH.ru и моделей FastEmbed при первом запуске.
- Токены ботов Telegram и MAX (если запускаете не только консольный режим).

## Установка и запуск

1. Установите PostgreSQL вместе с pgAdmin, запомните пароль пользователя `postgres` (порт по умолчанию `5432`).
2. Откройте pgAdmin, подключитесь к локальному серверу и создайте базу данных `hh_bot_db` (Databases -> Create -> Database).
3. Установите зависимости в PowerShell:

```powershell
python -m pip install -r requirements.txt
```

4. Создайте локальный `.env` из шаблона:

```powershell
Copy-Item .env.example .env
```

Откройте `.env` и пропишите пароль в `DATABASE_URL`, а в `ADMIN_IDS` укажите свой Telegram/MAX ID (чтобы работали `/stats` и `/update_kb`). При необходимости добавьте токены ботов и ключи Yandex Cloud.

5. Проверьте работу в консольном режиме (при первом старте создадутся таблицы `users` и `interactions` в PostgreSQL, скачаются документы и построится индекс в `data/qdrant_db`):

```powershell
python main.py --cli
```

6. Запуск мессенджеров, обновление базы и тесты:

```powershell
python main.py --telegram   # запуск Telegram-бота
python main.py --max        # запуск MAX-бота
python main.py --all        # запуск обоих ботов
python main.py --update-kb  # ручное обновление документации
python test_system.py       # проверка модулей
```

## Описание файла .env

Все настройки и секреты загружаются из `.env` функцией `load_env()` в `config.py` (вызывает `load_dotenv()` из `python-dotenv`).

| Поле | Назначение | Пример |
|---|---|---|
| `DATABASE_URL` | Строка подключения к PostgreSQL для SQLAlchemy | `postgresql+psycopg2://postgres:postgres_here@localhost:5432/hh_bot_db` |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота от @BotFather | `123456:EXAMPLE_ONLY` |
| `MAX_BOT_TOKEN` | Токен бота в мессенджере MAX | `example_max_token` |
| `MAX_API_URL` | Адрес MAX Bot API | `https://platform-api2.max.ru` |
| `ADMIN_IDS` | ID администраторов в Telegram/MAX через запятую | `123456789,987654321` |
| `YC_API_KEY` | API-ключ сервисного аккаунта Yandex Cloud | `example_api_key` |
| `YC_FOLDER_ID` | ID каталога в Yandex Cloud | `example_folder_id` |
| `YC_ASSISTANT_ID` | ID ассистента в Yandex AI Studio (необязательно) | `example_assistant_id` |
| `YC_MODEL` | Модель YandexGPT | `yandexgpt-5.1` |
| `DOCS_DIR` | Папка для локальных Markdown-файлов документации | `data/hh_docs` |
| `QDRANT_DIR` | Папка для локального векторного индекса Qdrant | `data/qdrant_db` |

Для запуска только в консольном режиме поля с токенами и ключами Yandex Cloud можно оставить пустыми, обязателен только `DATABASE_URL`.

## База данных

Модуль `database.py` работает через SQLAlchemy ORM и `psycopg2-binary`. Используются две таблицы:

- `users`: `id` (PK), `platform`, `platform_user_id`, `username`, `first_name`, `created_at`, `last_seen`. Пара `(platform, platform_user_id)` уникальна.
- `interactions`: `id` (PK), `user_id` (FK -> `users.id`), `user_query`, `bot_response`, `sources`, `created_at`.

## Обновление документации, поиск и права доступа

При старте, каждые 6 часов в фоне и по команде `/update_kb` `knowledge_base.py` запрашивает список `.md`-файлов в `hhru/api/docs`. Изменённые файлы обновляются, а файлы, которых больше нет в репозитории, удаляются и с диска. Дата обновления и SHA последнего коммита `docs` сохраняются в `data/hh_docs/kb_metadata.json`.

Для поиска используется локальный Qdrant: каждый фрагмент документации хранит плотный смысловой вектор и разреженный BM25-вектор с русским стеммингом, которые при запросе объединяются через `Fusion.RRF`. Благодаря этому бот находит нужный раздел как по точным кодам ошибок и методам, так и по разным словоформам и перефразированным вопросам.

Команды `/stats` и `/update_kb` в `bot_max.py` и `bot_telegram.py` проверяют `is_admin(user_id)`. Обычные пользователи не видят эти команды в подсказках и получают отказ при их вызове.
