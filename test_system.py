import unittest
from pathlib import Path
import database as db
from knowledge_base import KnowledgeBase
from yandex_assistant import YandexAssistant
from bot_max import MaxBot

class TestHHAssistantBot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Инициализируем базу данных для тестов
        db.init_db()
        cls.kb = KnowledgeBase()
        if cls.kb.get_stats()["files_count"] == 0:
            cls.kb.download_docs()
        cls.assistant = YandexAssistant(kb=cls.kb)

    def test_01_database_operations(self):
        """Тест базы данных: создание пользователя, сохранение и чтение истории."""
        user = db.get_or_create_user(
            platform="telegram",
            platform_user_id="test_user_999",
            username="test_dev",
            first_name="Иван"
        )
        self.assertIsNotNone(user["id"])
        self.assertEqual(user["username"], "test_dev")

        # Сохранение взаимодействия
        query = "Как получить список вакансий?"
        answer = "Используйте эндпоинт GET /vacancies с параметром text."
        sources = "[vacancies.md](https://github.com/hhru/api/blob/master/docs/vacancies.md)"
        
        interaction_id = db.save_interaction(user["id"], query, answer, sources)
        self.assertGreater(interaction_id, 0)

        # Проверка истории
        history = db.get_user_history(user["id"], limit=5)
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["user_query"], query)

        # Проверка статистики
        stats = db.get_database_stats()
        self.assertGreaterEqual(stats["total_users"], 1)
        self.assertGreaterEqual(stats["total_interactions"], 1)

    def test_02_knowledge_base_search(self):
        """Тест базы знаний: поиск по ключевым словам и проверка ссылок."""
        query = "авторизация OAuth2 токен"
        results = self.kb.search(query, top_k=2)
        
        self.assertGreater(len(results), 0)
        top = results[0]
        self.assertTrue(top["filename"].endswith(".md"))
        self.assertTrue(top["url"].startswith("https://github.com/hhru/api/blob/master/docs/"))
        self.assertTrue(len(top["text"]) > 0)

    def test_03_assistant_response_generation(self):
        """Тест формирования ответа ассистентом (с прикреплением источников)."""
        query = "Какие параметры принимает поиск вакансий?"
        answer, sources = self.assistant.generate_response(query)
        
        self.assertTrue(len(answer) > 0)
        self.assertGreater(len(sources), 0)
        self.assertIn("github.com/hhru/api", sources[0])

    def test_04_max_bot_command_handlers(self):
        """Тест обработчика сообщений и команд бота MAX."""
        bot = MaxBot()
        
        # Проверка команды /start
        start_res = bot.handle_message("chat_1", "u_1", "User1", "/start")
        self.assertIn("Привет!", start_res)

        # Проверка команды /help
        help_res = bot.handle_message("chat_1", "u_1", "User1", "/help")
        self.assertIn("Справка", help_res)

        # Проверка команды /stats
        stats_res = bot.handle_message("chat_1", "u_1", "User1", "/stats")
        self.assertIn("Всего пользователей", stats_res)

        # Проверка обычного вопроса
        q_res = bot.handle_message("chat_1", "u_1", "User1", "Как получить резюме?")
        self.assertTrue(len(q_res) > 20)

if __name__ == "__main__":
    unittest.main()
