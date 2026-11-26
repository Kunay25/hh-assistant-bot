import telebot
from telebot import types
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, is_admin
import database as db
from yandex_assistant import YandexAssistant


class TelegramBot:
    """
    Интеграция с Telegram на библиотеке pyTelegramBotAPI.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        assistant: Optional[YandexAssistant] = None,
    ):
        self.token = token or TELEGRAM_BOT_TOKEN
        self.assistant = assistant if assistant is not None else YandexAssistant()
        db.init_db()

        if self.token:
            self.bot = telebot.TeleBot(self.token)
            self._register_handlers()
        else:
            self.bot = None

    def _register_handlers(self) -> None:
        bot = self.bot

        @bot.message_handler(commands=["start"])
        def handle_start(message: types.Message):
            db.get_or_create_user(
                platform="telegram",
                platform_user_id=str(message.from_user.id),
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )
            welcome_text = (
                f"Привет, {message.from_user.first_name or 'разработчик'}!\n\n"
                "Я чат-бот для ответов по базе знаний API HeadHunter (hh.ru).\n"
                "Помогу найти нужные методы, параметры авторизации, схемы откликов и вакансий.\n\n"
                "Команды:\n"
                "/help - подробная справка\n"
                "/history - история ваших последних 5 запросов\n"
            )
            if is_admin(message.from_user.id):
                welcome_text += (
                    "\nКоманды администратора:\n"
                    "/stats - статистика базы данных и версия БЗ\n"
                    "/update_kb - обновить базу знаний из репозитория hhru/api\n"
                )
            welcome_text += "\nНапишите ваш технический вопрос обычным сообщением."
            bot.reply_to(message, welcome_text)

        @bot.message_handler(commands=["help"])
        def handle_help(message: types.Message):
            help_text = (
                "**Как пользоваться ботом API HH.ru:**\n\n"
                "Задавайте вопросы на обычном языке, например:\n"
                "- Как получить список резюме кандидата?\n"
                "- Какой формат авторизации у работодателя?\n"
                "- Что делать при ошибке 403 forbidden?\n\n"
                "Доступные команды:\n"
                "/history - посмотреть предыдущие вопросы"
            )
            if is_admin(message.from_user.id):
                help_text += (
                    "\n\nКоманды администратора:\n"
                    "/stats - статистика по пользователям и версия документации\n"
                    "/update_kb - синхронизировать базу знаний с GitHub docs"
                )
            bot.reply_to(message, help_text, parse_mode="Markdown")

        @bot.message_handler(commands=["history"])
        def handle_history(message: types.Message):
            user = db.get_or_create_user(
                platform="telegram",
                platform_user_id=str(message.from_user.id),
            )
            history = db.get_user_history(user["id"], limit=5)
            if not history:
                bot.reply_to(message, "Ваша история запросов пока пуста.")
                return

            lines = ["**Ваши последние запросы:**\n"]
            for idx, item in enumerate(history, 1):
                query = item["user_query"]
                date = item["created_at"]
                lines.append(f"{idx}. `[{date}]` {query}")
            bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

        @bot.message_handler(commands=["stats"])
        def handle_stats(message: types.Message):
            if not is_admin(message.from_user.id):
                bot.reply_to(message, "Команда /stats доступна только администратору.")
                return

            stats = db.get_database_stats()
            kb_stats = self.assistant.kb.get_stats()
            platforms_str = ", ".join(f"{k}: {v}" for k, v in stats["platforms"].items())
            text = (
                "**Статистика системы:**\n"
                f"- Всего зарегистрировано пользователей: {stats['total_users']}\n"
                f"- Всего взаимодействий в БД: {stats['total_interactions']}\n"
                f"- Активность по платформам: {platforms_str or 'нет'}\n\n"
                "**База знаний:**\n"
                f"- Документов: {kb_stats['files_count']}, фрагментов: {kb_stats['chunks_count']}\n"
                f"- Версия: `{kb_stats['docs_version']}`\n"
                f"- Обновлена: `{kb_stats['updated_at']}`\n"
                f"- Поиск: {kb_stats['search_mode']}"
            )
            bot.reply_to(message, text, parse_mode="Markdown")

        @bot.message_handler(commands=["update_kb"])
        def handle_update_kb(message: types.Message):
            if not is_admin(message.from_user.id):
                bot.reply_to(message, "Команда /update_kb доступна только администратору.")
                return

            bot.reply_to(message, "Начинаю синхронизацию документации с GitHub...")
            res = self.assistant.update_knowledge_base()
            if res.get("success"):
                bot.reply_to(
                    message,
                    f"База знаний обновлена.\n"
                    f"- Загружено/обновлено файлов: {res.get('downloaded', 0)}\n"
                    f"- Удалено устаревших файлов: {res.get('deleted', 0)}\n"
                    f"- Всего файлов: {res.get('files_count', 0)}\n"
                    f"- Индексных фрагментов: {res.get('total_chunks', 0)}\n"
                    f"- Версия: {res.get('docs_version')}\n"
                    f"- Дата: {res.get('updated_at')}",
                )
            else:
                bot.reply_to(message, f"Ошибка обновления: {res.get('error')}")

        @bot.message_handler(content_types=["text"])
        def handle_text(message: types.Message):
            user_text = message.text.strip()

            user = db.get_or_create_user(
                platform="telegram",
                platform_user_id=str(message.from_user.id),
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )

            bot.send_chat_action(message.chat.id, "typing")

            answer, sources = self.assistant.generate_response(user_text)
            sources_str = ", ".join(sources) if sources else ""

            db.save_interaction(user["id"], user_text, answer, sources_str)

            try:
                bot.reply_to(message, answer, parse_mode="Markdown")
            except Exception:
                bot.reply_to(message, answer)

    def run(self) -> None:
        if not self.bot:
            print("[Telegram Bot] Токен TELEGRAM_BOT_TOKEN не указан в .env файле.")
            print("[Telegram Bot] Добавьте токен от @BotFather для запуска.")
            return

        print("[Telegram Bot] Бот запущен в режиме polling. Ожидание сообщений...")
        self.bot.infinity_polling(timeout=10, long_polling_timeout=5)
