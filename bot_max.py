import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

import requests

from config import MAX_API_URL, MAX_BOT_TOKEN, is_admin
import database as db
from yandex_assistant import YandexAssistant

CERTS_DIR = Path(__file__).resolve().parent / "certs"
MAX_CA_BUNDLE = CERTS_DIR / "max_ca_bundle.pem"


def _get_ssl_verify() -> Union[str, bool]:
    """Возвращает путь к бандлу сертификатов Минцифры или собирает его из .crt."""
    if MAX_CA_BUNDLE.exists():
        return str(MAX_CA_BUNDLE)
    crt_files = sorted(CERTS_DIR.glob("*.crt"))
    if crt_files:
        try:
            combined = "\n".join(p.read_text(encoding="utf-8") for p in crt_files)
            MAX_CA_BUNDLE.write_text(combined, encoding="utf-8")
            return str(MAX_CA_BUNDLE)
        except OSError:
            pass
    return True


class MaxBot:
    """
    Интеграция с мессенджером MAX через Bot API.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        api_url: Optional[str] = None,
        assistant: Optional[YandexAssistant] = None,
    ):
        self.token = token or MAX_BOT_TOKEN
        self.api_url = (api_url or MAX_API_URL).rstrip("/")
        self.assistant = assistant if assistant is not None else YandexAssistant()
        self.running = False
        db.init_db()

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"{self.token}",
            "Content-Type": "application/json",
        }

    def send_message(self, chat_id: int, text: str) -> bool:
        if not self.token:
            print("[MAX] Токен не указан")
            return False

        url = f"{self.api_url}/messages"
        verify_opt = _get_ssl_verify()

        try:
            for i in range(0, len(text), 3900):
                part = text[i : i + 3900]
                response = requests.post(
                    url,
                    headers=self._get_headers(),
                    params={"chat_id": chat_id},
                    json={"text": part},
                    timeout=15,
                    verify=verify_opt,
                )

                if response.status_code != 200:
                    print(f"[MAX] Ошибка отправки: {response.status_code}")
                    print(response.text[:300])
                    return False

            return True

        except requests.RequestException as e:
            print(f"[MAX] Ошибка соединения: {e}")
            return False

    def handle_message(self, chat_id: str, user_id: str, username: str, text: str) -> str:
        """
        Обработка входящего сообщения от пользователя MAX.
        """
        text = text.strip()
        admin = is_admin(user_id)
        user = db.get_or_create_user(
            platform="max",
            platform_user_id=user_id,
            username=username,
            first_name=username,
        )

        if text.startswith("/start"):
            base_text = (
                "Привет! Я чат-бот MAX для разработчиков по API HeadHunter (hh.ru).\n\n"
                "Отвечаю на вопросы по авторизации OAuth2, поиску вакансий, резюме и откликам.\n\n"
                "Команды:\n"
                "/help - справка\n"
                "/history - история ваших последних вопросов\n"
            )
            if admin:
                base_text += (
                    "\nКоманды администратора:\n"
                    "/stats - статистика базы данных и версия документации\n"
                    "/update_kb - обновить базу знаний из GitHub\n"
                )
            return base_text + "\nНапишите свой вопрос по API HH.ru."

        elif text.startswith("/help"):
            help_text = (
                "Справка по чат-боту MAX (API HH.ru)\n\n"
                "Напишите вопрос обычным текстом, например:\n"
                "- Как получить токен авторизации OAuth2?\n"
                "- Как искать вакансии по региону и зарплате?\n"
                "- Какие ошибки возвращает API при создании отклика?\n\n"
                "Команды:\n"
                "/history - показать ваши последние запросы"
            )
            if admin:
                help_text += (
                    "\n\nКоманды администратора:\n"
                    "/stats - общая статистика и версия базы знаний\n"
                    "/update_kb - синхронизировать docs из репозитория hhru/api"
                )
            return help_text

        elif text.startswith("/history"):
            history = db.get_user_history(user["id"], limit=5)
            if not history:
                return "У вас пока нет сохраненной истории запросов."

            lines = ["Ваша история последних запросов:"]
            for idx, item in enumerate(history, 1):
                query = item["user_query"]
                date = item["created_at"]
                lines.append(f"{idx}. [{date}] Вопрос: {query}")
            return "\n".join(lines)

        elif text.startswith("/stats"):
            if not admin:
                return "Команда /stats доступна только администратору."
            stats = db.get_database_stats()
            kb_stats = self.assistant.kb.get_stats()
            platforms_str = ", ".join(f"{k}: {v}" for k, v in stats["platforms"].items())
            return (
                f"Статистика бота:\n"
                f"- Всего пользователей: {stats['total_users']}\n"
                f"- Всего обработано запросов: {stats['total_interactions']}\n"
                f"- Платформы: {platforms_str or 'нет данных'}\n\n"
                f"База знаний:\n"
                f"- Файлов: {kb_stats['files_count']}, фрагментов: {kb_stats['chunks_count']}\n"
                f"- Версия (commit): {kb_stats['docs_version']}\n"
                f"- Обновлена: {kb_stats['updated_at']}\n"
                f"- Поиск: {kb_stats['search_mode']}"
            )

        elif text.startswith("/update_kb"):
            if not admin:
                return "Команда /update_kb доступна только администратору."
            result = self.assistant.update_knowledge_base()
            if result.get("success"):
                return (
                    f"База знаний обновлена.\n"
                    f"- Обновлено файлов: {result.get('downloaded', 0)}\n"
                    f"- Удалено устаревших: {result.get('deleted', 0)}\n"
                    f"- Всего файлов: {result.get('files_count', 0)}\n"
                    f"- Версия: {result.get('docs_version')}\n"
                    f"- Дата: {result.get('updated_at')}"
                )
            else:
                return f"Ошибка обновления: {result.get('error', 'неизвестная ошибка')}"

        answer, sources = self.assistant.generate_response(text)
        sources_str = ", ".join(sources) if sources else ""
        db.save_interaction(user["id"], text, answer, sources_str)
        return answer

    def process_update(self, update: Dict[str, Any]) -> None:
        event_type = update.get("update_type")

        if event_type == "bot_started":
            chat_id = update.get("chat_id")
            user = update.get("user") or {}

            if chat_id is not None and user.get("user_id") is not None:
                user_id = str(user["user_id"])
                username = user.get("first_name") or "Пользователь"

                answer = self.handle_message(str(chat_id), user_id, username, "/start")
                self.send_message(chat_id, answer)
            return

        if event_type != "message_created":
            return

        message = update.get("message") or {}
        sender = message.get("sender") or {}

        if not sender or sender.get("is_bot"):
            return

        recipient = message.get("recipient") or {}
        chat_id = recipient.get("chat_id")
        user_id = sender.get("user_id")
        username = sender.get("first_name") or "Пользователь"
        text = (message.get("body") or {}).get("text") or ""

        if chat_id is None or user_id is None or not text.strip():
            return

        print(f"[MAX] Получено сообщение: {text}")

        answer = self.handle_message(str(chat_id), str(user_id), username, text)
        self.send_message(chat_id, answer)

    def poll_updates(self) -> None:
        if not self.token:
            print("[MAX] Укажите MAX_BOT_TOKEN в .env")
            return

        print("[MAX] Бот запущен. Ожидание сообщений...")
        self.running = True
        marker = None
        verify_opt = _get_ssl_verify()

        while self.running:
            try:
                params = {
                    "timeout": 20,
                    "types": "message_created,bot_started",
                }

                if marker is not None:
                    params["marker"] = marker

                response = requests.get(
                    f"{self.api_url}/updates",
                    headers=self._get_headers(),
                    params=params,
                    timeout=30,
                    verify=verify_opt,
                )

                if response.status_code == 401:
                    print("[MAX] Неверный токен!")
                    break

                if response.status_code != 200:
                    print(f"[MAX] Ошибка: {response.status_code}")
                    print(response.text[:300])
                    time.sleep(3)
                    continue

                data = response.json()

                for update in data.get("updates", []):
                    try:
                        self.process_update(update)
                    except Exception as e:
                        print(f"[MAX] Ошибка обработки: {e}")

                marker = data.get("marker", marker)

            except requests.RequestException as e:
                print(f"[MAX] Ошибка сети: {e}")
                time.sleep(3)

            except ValueError as e:
                print(f"[MAX] Ошибка ответа API: {e}")
                time.sleep(3)

    def interactive_cli(self) -> None:
        """
        Консольная эмуляция чата с ботом MAX для локальной отладки.
        """
        print("\n--- Режим диалога с ботом MAX ---")
        print("Введите вопрос или команду (/help, /history). Для выхода введите 'exit'.\n")
        user_id = "test_max_dev_1"
        username = "Developer_Ivan"

        while True:
            try:
                user_input = input("Вы [MAX]: ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit"):
                    break

                reply = self.handle_message(chat_id="chat_1", user_id=user_id, username=username, text=user_input)
                print(f"\nБот MAX:\n{reply}\n")
            except (KeyboardInterrupt, EOFError):
                break
        print("Сессия MAX завершена.")
