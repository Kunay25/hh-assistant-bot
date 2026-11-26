import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import config
import database as db
from bot_max import MaxBot
from knowledge_base import KnowledgeBase
from yandex_assistant import YandexAssistant


class _FakeHTTPResponse:
    def __init__(self, payload_bytes: bytes):
        self._buf = BytesIO(payload_bytes)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestHHAssistantBot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
            first_name="Иван",
        )
        self.assertIsNotNone(user["id"])
        self.assertEqual(user["username"], "test_dev")

        query = "Как получить список вакансий?"
        answer = "Используйте эндпоинт GET /vacancies с параметром text."
        sources = "[vacancies.md](https://github.com/hhru/api/blob/master/docs/vacancies.md)"

        interaction_id = db.save_interaction(user["id"], query, answer, sources)
        self.assertGreater(interaction_id, 0)

        history = db.get_user_history(user["id"], limit=5)
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["user_query"], query)

        stats = db.get_database_stats()
        self.assertGreaterEqual(stats["total_users"], 1)
        self.assertGreaterEqual(stats["total_interactions"], 1)

    def test_02_hybrid_search_semantics_and_morphology(self):
        """Тест гибридного поиска (смысловые запросы + разные словоформы)."""
        # Смысловой перефразированный запрос без слов "authorization" / "OAuth"
        auth_results = self.kb.search("как залогиниться и получить ключ доступа?", top_k=3)
        self.assertGreater(len(auth_results), 0)
        auth_files = [r["filename"] for r in auth_results]
        self.assertTrue(
            any("authorization" in fn for fn in auth_files),
            f"Ожидался файл авторизации, получено: {auth_files}",
        )

        # Запрос с другими формами слов и смыслом про 403 / запрет доступа
        err_results = self.kb.search("почему сервер не пускает и выдает ошибку 403?", top_k=3)
        self.assertGreater(len(err_results), 0)
        err_files = [r["filename"] for r in err_results]
        self.assertTrue(
            any(fn in ("errors.md", "authorization.md", "authorization_for_user.md") for fn in err_files),
            f"Ожидались errors/authorization, получено: {err_files}",
        )

        # Морфология и перефразирование отклика на работу
        neg_results = self.kb.search("откликнуться на предложение о работе", top_k=3)
        self.assertGreater(len(neg_results), 0)
        neg_files = [r["filename"] for r in neg_results]
        self.assertTrue(
            any("negotiations" in fn for fn in neg_files),
            f"Ожидался файл про отклики, получено: {neg_files}",
        )

        # Кросс-языковой запрос по вакансиям и зарплате
        vac_results = self.kb.search("поиск вакансий по зарплате", top_k=3)
        self.assertGreater(len(vac_results), 0)
        vac_files = [r["filename"] for r in vac_results]
        self.assertTrue(
            any("vacanc" in fn for fn in vac_files),
            f"Ожидался файл про вакансии, получено: {vac_files}",
        )

    def test_03_sync_deletes_obsolete_files_and_saves_metadata(self):
        """Тест сверки списка файлов при обновлении: удаление устаревших и сохранение версии/даты."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = Path(tmpdir) / "hh_docs"
            qdrant_dir = Path(tmpdir) / "qdrant_db"
            docs_dir.mkdir(parents=True, exist_ok=True)

            # Создаем устаревший документ, которого уже нет в исходном репозитории
            obsolete_file = docs_dir / "old_deprecated.md"
            obsolete_file.write_text("# Устаревший метод\nСтарые данные", encoding="utf-8")

            kb_tmp = KnowledgeBase(docs_dir=docs_dir, qdrant_dir=qdrant_dir, use_vector_search=False)

            fake_contents = json.dumps([
                {
                    "name": "actual_doc.md",
                    "sha": "111222333",
                    "download_url": "https://raw.githubusercontent.com/hhru/api/master/docs/actual_doc.md",
                }
            ]).encode("utf-8")
            fake_md = "# Актуальный метод\nОписание нового эндпоинта API.".encode("utf-8")
            fake_commits = json.dumps([
                {"sha": "abcdef1234567890", "commit": {"committer": {"date": "2026-11-25T12:00:00Z"}}}
            ]).encode("utf-8")

            def fake_urlopen(req, timeout=15):
                url = req.full_url if hasattr(req, "full_url") else str(req)
                if "commits" in url:
                    return _FakeHTTPResponse(fake_commits)
                if "contents/docs" in url:
                    return _FakeHTTPResponse(fake_contents)
                return _FakeHTTPResponse(fake_md)

            with patch("knowledge_base.urllib.request.urlopen", side_effect=fake_urlopen):
                res = kb_tmp.download_docs()

            self.assertTrue(res["success"])
            self.assertEqual(res["downloaded"], 1)
            self.assertEqual(res["deleted"], 1)
            self.assertEqual(res["docs_version"], "abcdef1")
            self.assertFalse(obsolete_file.exists())
            self.assertTrue((docs_dir / "actual_doc.md").exists())

            stats = kb_tmp.get_stats()
            self.assertEqual(stats["docs_version"], "abcdef1")
            self.assertEqual(stats["commit_date"], "2026-11-25T12:00:00Z")
            self.assertIn("UTC", stats["updated_at"])

            # Убеждаемся, что устаревший документ больше не находится
            search_old = kb_tmp.search("Устаревший метод Старые данные", top_k=3)
            self.assertFalse(any(c["filename"] == "old_deprecated.md" for c in search_old))

    def test_04_assistant_response_generation(self):
        """Тест формирования ответа ассистентом (с прикреплением источников)."""
        query = "Какие параметры принимает поиск вакансий?"
        answer, sources = self.assistant.generate_response(query)

        self.assertTrue(len(answer) > 0)
        self.assertGreater(len(sources), 0)
        self.assertIn("github.com/hhru/api", sources[0])

    def test_05_admin_access_control(self):
        """Тест разграничения прав: /stats и /update_kb доступны только администратору."""
        bot = MaxBot(assistant=self.assistant)

        with patch.object(config, "ADMIN_IDS", {"admin_42"}):
            # Обычный пользователь
            start_user = bot.handle_message("chat_1", "regular_user_1", "User1", "/start")
            self.assertIn("Привет!", start_user)
            self.assertNotIn("/update_kb", start_user)

            help_user = bot.handle_message("chat_1", "regular_user_1", "User1", "/help")
            self.assertIn("Справка", help_user)
            self.assertNotIn("/update_kb", help_user)

            denied_stats = bot.handle_message("chat_1", "regular_user_1", "User1", "/stats")
            self.assertIn("только администратору", denied_stats)

            denied_update = bot.handle_message("chat_1", "regular_user_1", "User1", "/update_kb")
            self.assertIn("только администратору", denied_update)

            # Администратор
            help_admin = bot.handle_message("chat_1", "admin_42", "Admin", "/help")
            self.assertIn("/update_kb", help_admin)

            stats_admin = bot.handle_message("chat_1", "admin_42", "Admin", "/stats")
            self.assertIn("Всего пользователей", stats_admin)
            self.assertIn("Версия", stats_admin)


if __name__ == "__main__":
    unittest.main()
