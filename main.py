import sys
import argparse
import threading
from config import TELEGRAM_BOT_TOKEN, MAX_BOT_TOKEN
import database as db
from knowledge_base import KnowledgeBase
from yandex_assistant import YandexAssistant
from bot_telegram import TelegramBot
from bot_max import MaxBot
def run_cli_chat(assistant: YandexAssistant) -> None:
    """
    Интерактивный режим диалога прямо в терминале.
    """
    print("\n" + "=" * 60)
    print("🤖 Интеллектуальный помощник по API HeadHunter (hh.ru)")
    print("Режим: Консольный интерфейс (CLI)")
    print("Доступные команды: /help, /history, /stats, /update_kb, exit")
    print("=" * 60 + "\n")
    
    # Создаем или получаем тестового пользователя для консоли
    user = db.get_or_create_user(
        platform="cli",
        platform_user_id="cli_dev_user",
        username="console_user",
        first_name="Разработчик"
    )

    while True:
        try:
            query = input("Вы: ").strip()
            if not query:
                continue
            if query.lower() in ("exit", "quit", "выход"):
                print("Завершение работы.")
                break

            if query == "/help":
                print("\n[Справка] Задайте любой технический вопрос по API hh.ru.")
                print("Примеры: 'Как создать отклик?', 'Как работает OAuth авторизация?', 'Коды ошибок'\n")
                continue

            if query == "/history":
                history = db.get_user_history(user["id"], limit=5)
                print("\n[История ваших запросов]:")
                for idx, h in enumerate(history, 1):
                    print(f"{idx}. [{h['created_at']}] {h['user_query']}")
                print()
                continue

            if query == "/stats":
                stats = db.get_database_stats()
                print(f"\n[Статистика]: Пользователей={stats['total_users']}, Запросов={stats['total_interactions']}, Платформы={stats['platforms']}\n")
                continue

            if query == "/update_kb":
                print("\n[Обновление] Скачивание документации из репозитория GitHub...")
                res = assistant.update_knowledge_base()
                print(f"[Результат] {res}\n")
                continue

            print("\nПоиск информации и формирование ответа...")
            answer, sources = assistant.generate_response(query)
            
            # Сохраняем в БД
            sources_str = ", ".join(sources) if sources else ""
            db.save_interaction(user["id"], query, answer, sources_str)

            print(f"\nБот:\n{answer}\n")
            print("-" * 60)

        except (KeyboardInterrupt, EOFError):
            print("\nВыход из программы.")
            break

def start_telegram_bot() -> None:
    bot = TelegramBot()
    bot.run()

def start_max_bot() -> None:
    bot = MaxBot()
    bot.poll_updates()

def main():
    parser = argparse.ArgumentParser(description="Чат-бот ассистент по API HH.ru на базе Yandex Assistant")
    parser.add_argument("--cli", action="store_true", help="Запустить консольный интерактивный режим")
    parser.add_argument("--telegram", action="store_true", help="Запустить Telegram бота")
    parser.add_argument("--max", action="store_true", help="Запустить MAX бота")
    parser.add_argument("--all", action="store_true", help="Запустить всех доступных ботов одновременно")
    parser.add_argument("--update-kb", action="store_true", help="Обновить базу знаний из GitHub и завершить работу")
    args = parser.parse_args()

    # 1. Инициализация базы данных
    db.init_db()

    # 2. Проверка базы знаний
    kb = KnowledgeBase()
    stats = kb.get_stats()
    if stats["files_count"] == 0:
        print("[Инициализация] Локальная база знаний пуста. Загрузка docs из GitHub...")
        kb.download_docs()

    assistant = YandexAssistant(kb=kb)

    # Режим обновления базы знаний
    if args.update_kb:
        print("[Обновление] Запуск загрузки документации из репозитория hhru/api...")
        res = assistant.update_knowledge_base()
        print(f"[Результат]: {res}")
        return
    # Автоматическое обновление документации
    kb.start_auto_updates(interval_hours=6)
    # Явный запуск CLI
    if args.cli:
        run_cli_chat(assistant)
        return

    # Запуск Telegram
    if args.telegram:
        start_telegram_bot()
        return

    # Запуск MAX
    if args.max:
        start_max_bot()
        return

    # Запуск всех ботов
    if args.all:
        threads = []
        if TELEGRAM_BOT_TOKEN:
            t1 = threading.Thread(target=start_telegram_bot, daemon=True)
            t1.start()
            threads.append(t1)
        if MAX_BOT_TOKEN:
            t2 = threading.Thread(target=start_max_bot, daemon=True)
            t2.start()
            threads.append(t2)
        
        if not threads:
            print("[Внимание] Токены TELEGRAM_BOT_TOKEN и MAX_BOT_TOKEN не заданы. Переключение в CLI-режим.")
            run_cli_chat(assistant)
        else:
            print("[Система] Все сервисы запущены. Ожидание сообщений (Ctrl+C для остановки)...")
            try:
                for t in threads:
                    t.join()
            except KeyboardInterrupt:
                print("\nОстановка приложения.")
        return

    # Запуск по умолчанию при отсутствии аргументов:
    if TELEGRAM_BOT_TOKEN and not MAX_BOT_TOKEN:
        start_telegram_bot()
    elif MAX_BOT_TOKEN and not TELEGRAM_BOT_TOKEN:
        start_max_bot()
    elif TELEGRAM_BOT_TOKEN and MAX_BOT_TOKEN:
        print("[Система] Обнаружены токены для Telegram и MAX. Запуск обоих ботов...")
        t1 = threading.Thread(target=start_telegram_bot, daemon=True)
        t1.start()
        start_max_bot()
    else:
        # Если ни один токен пока не настроен, открываем дружелюбный CLI режим
        print("[Инфо] Токены для Telegram и MAX пока не настроены в .env файле.")
        print("[Инфо] Запуск удобного локального интерактивного режима для тестирования.")
        run_cli_chat(assistant)

if __name__ == "__main__":
    main()
