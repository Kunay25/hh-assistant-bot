import time
import requests
from typing import Optional, Dict, Any
from config import MAX_BOT_TOKEN, MAX_API_URL
import database as db
from yandex_assistant import YandexAssistant
import time
import requests
from typing import Optional, Dict, Any
from pathlib import Path
MAX_CA_BUNDLE = Path(__file__).resolve().parent / "certs" / "max_ca_bundle.pem"
class MaxBot:
    """
    Класс интеграции с корпоративным мессенджером MAX через Bot API.
    """
    def __init__(self, token: Optional[str] = None, api_url: Optional[str] = None):
        self.token = token or MAX_BOT_TOKEN
        self.api_url = (api_url or MAX_API_URL).rstrip("/")
        self.assistant = YandexAssistant()
        self.running = False
        db.init_db()

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"{self.token}",
            "Content-Type": "application/json"
        }

    def send_message(self, chat_id: int, text: str) -> bool:
        if not self.token:
            print("[MAX] Токен не указан")
            return False

        url = f"{self.api_url}/messages"

        try:
            for i in range(0, len(text), 3900):
                part = text[i:i + 3900]

                response = requests.post(
    url,
    headers=self._get_headers(),
    params={"chat_id": chat_id},
    json={"text": part},
    timeout=15,
    verify=str(MAX_CA_BUNDLE)
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
        Обработка входящего текстового сообщения от пользователя MAX.
        """
        text = text.strip()
        user = db.get_or_create_user(
            platform="max",
            platform_user_id=user_id,
            username=username,
            first_name=username
        )
        
        # Обработка команд
        if text.startswith("/start"):
            return (
                "👋 Привет! Я чат-бот MAX для разработчиков по API HeadHunter (hh.ru).\n\n"
                "Я умею отвечать на технические вопросы по интеграции, поиску вакансий, "
                "авторизации OAuth2, работе с резюме и откликами.\n\n"
                "Команды:\n"
                "/help — справка\n"
                "/history — история ваших последних вопросов\n"
                "/stats — статистика базы данных\n"
                "/update_kb — обновить базу знаний из GitHub репозитория\n\n"
                "Задайте любой вопрос разработчика по API HH.ru!"
            )
            
        elif text.startswith("/help"):
            return (
                "ℹ️ **Справка по чат-боту MAX (API HH.ru)**\n\n"
                "Просто напишите свой вопрос обычным текстом, например:\n"
                "• «Как получить токен авторизации OAuth2?»\n"
                "• «Как искать вакансии по региону и зарплате?»\n"
                "• «Какие ошибки возвращает API при создании отклика?»\n\n"
                "Команды:\n"
                "/history — показать ваши последние запросы\n"
                "/stats — общая статистика пользователей\n"
                "/update_kb — загрузить свежие docs из репозитория hhru/api"
            )
            
        elif text.startswith("/history"):
            history = db.get_user_history(user["id"], limit=5)
            if not history:
                return "У вас пока нет сохраненной истории запросов."
            
            lines = ["📜 Ваша история последних запросов:"]
            for idx, item in enumerate(history, 1):
                query = item["user_query"]
                date = item["created_at"]
                lines.append(f"{idx}. [{date}] Вопрос: {query}")
            return "\n".join(lines)
            
        elif text.startswith("/stats"):
            stats = db.get_database_stats()
            platforms_str = ", ".join(f"{k}: {v}" for k, v in stats["platforms"].items())
            return (
                f"📊 Статистика бота:\n"
                f"• Всего пользователей: {stats['total_users']}\n"
                f"• Всего обработано запросов: {stats['total_interactions']}\n"
                f"• Платформы: {platforms_str or 'нет данных'}"
            )
            
        elif text.startswith("/update_kb"):
            result = self.assistant.update_knowledge_base()
            if result.get("success"):
                return f"✅ База знаний успешно обновлена! Скачано файлов: {result.get('downloaded', 0)}."
            else:
                return f"⚠️ Ошибка обновления: {result.get('error', 'неизвестная ошибка')}"
        
        # Обычный вопрос разработчика
        answer, sources = self.assistant.generate_response(text)
        sources_str = ", ".join(sources) if sources else ""
        db.save_interaction(user["id"], text, answer, sources_str)
        return answer
    def process_update(self, update: Dict[str, Any]) -> None:
        event_type = update.get("update_type")

        # Пользователь нажал Start в MAX
        if event_type == "bot_started":
            chat_id = update.get("chat_id")
            user = update.get("user") or {}

            if chat_id is not None and user.get("user_id") is not None:
                user_id = str(user["user_id"])
                username = user.get("first_name") or "Пользователь"

                answer = self.handle_message(
                    str(chat_id), user_id, username, "/start"
                )
                self.send_message(chat_id, answer)
            return

        # Обрабатываем только новые сообщения
        if event_type != "message_created":
            return

        message = update.get("message") or {}
        sender = message.get("sender") or {}

        # Не отвечаем на сообщения самого бота
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

        answer = self.handle_message(
            str(chat_id),
            str(user_id),
            username,
            text
        )

        self.send_message(chat_id, answer)
    def poll_updates(self) -> None:
        if not self.token:
            print("[MAX] Укажи MAX_BOT_TOKEN в .env")
            return

        print("[MAX] Бот запущен. Ожидание сообщений...")
        self.running = True
        marker = None

        while self.running:
            try:
                params = {
                    "timeout": 20,
                    "types": "message_created,bot_started"
                }

                if marker is not None:
                    params["marker"] = marker

                response = requests.get(
    f"{self.api_url}/updates",
    headers=self._get_headers(),
    params=params,
    timeout=30,
    verify=str(MAX_CA_BUNDLE)
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
        Консольная эмуляция чата с ботом MAX (удобно для локального тестирования и отладки).
        """
        print("\n--- Режим диалога с ботом MAX ---")
        print("Введите вопрос или команду (/help, /history, /stats, /update_kb). Для выхода введите 'exit'.\n")
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
