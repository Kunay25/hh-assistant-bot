import json
import requests
from typing import Tuple, List, Dict, Any, Optional
from config import YC_API_KEY, YC_FOLDER_ID, YC_MODEL
from knowledge_base import KnowledgeBase

SYSTEM_PROMPT = (
    "Ты технический чат-бот ассистент по базе знаний API HeadHunter (hh.ru). "
    "Твоя задача — помогать разработчикам, отвечая на их вопросы по интеграции с API hh.ru. "
    "Используй предоставленный контекст документации. Отвечай понятно, структурированно, "
    "указывай точные HTTP-методы, эндпоинты, параметры запросов и коды ответов, где это применимо. "
    "Не выдумывай параметры, которых нет в документации."
)

class YandexAssistant:
    def __init__(self, kb: Optional[KnowledgeBase] = None):
        self.kb = kb or KnowledgeBase()
        self.api_key = YC_API_KEY
        self.folder_id = YC_FOLDER_ID
        self.model = YC_MODEL or "yandexgpt"
        self.api_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def update_knowledge_base(self) -> Dict[str, Any]:
        """
        Загружает обновления документации из GitHub и обновляет индекс.
        """
        return self.kb.download_docs()

    def generate_response(self, user_query: str) -> Tuple[str, List[str]]:
        """
        Основной метод обработки вопроса разработчика:
        1. Поиск релевантных статей в базе знаний HH.ru.
        2. Формирование контекста.
        3. Запрос к YandexGPT (или локальный RAG-ответ при отсутствии ключа).
        4. Добавление ссылок на документацию.
        """
        # 1. Ищем релевантные куски документации
        relevant_chunks = self.kb.search(user_query, top_k=3)
        
        sources: List[str] = []
        context_parts = []
        for ch in relevant_chunks:
            source_link = f"[{ch['filename']} — {ch['title']}]({ch['url']})"
            if source_link not in sources:
                sources.append(source_link)
            context_parts.append(f"--- Раздел: {ch['title']} ({ch['filename']}) ---\n{ch['text']}")

        context_text = "\n\n".join(context_parts)

        # 2. Пробуем получить ответ от YandexGPT через облачный API
        yandex_answer = None
        if self.api_key and self.folder_id:
            try:
                yandex_answer = self._call_yandex_gpt(user_query, context_text)
            except Exception as e:
                print(f"[YandexAssistant] Ошибка обращения к YandexGPT API: {e}")
                yandex_answer = None

        # 3. Если API Yandex не настроен или вернул ошибку, используем локальный ответ по документации
        if not yandex_answer:
            yandex_answer = self._build_local_fallback_answer(user_query, relevant_chunks)

        # 4. Прикрепляем блок источников в конец ответа
        final_answer = yandex_answer
        if sources:
            sources_block = "\n\n📌 Полезные разделы документации HH.ru:\n" + "\n".join(f"• {s}" for s in sources)
            final_answer += sources_block

        return final_answer, sources

    def _call_yandex_gpt(self, query: str, context: str) -> str:
        """
        Отправляет запрос в YandexGPT с контекстом документации.
        """
        model_uri = f"gpt://{self.folder_id}/{self.model}/latest"
        
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "x-folder-id": self.folder_id,
            "Content-Type": "application/json"
        }
        
        user_prompt = f"Контекст из официальной документации HH.ru API:\n{context}\n\nВопрос разработчика:\n{query}"
        
        payload = {
            "modelUri": model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": 0.2,
                "maxTokens": "1500"
            },
            "messages": [
                {"role": "system", "text": SYSTEM_PROMPT},
                {"role": "user", "text": user_prompt}
            ]
        }
        
        response = requests.post(self.api_url, headers=headers, json=payload, timeout=25)
        response.raise_for_status()
        data = response.json()
        
        alternatives = data.get("result", {}).get("alternatives", [])
        if alternatives:
            return alternatives[0].get("message", {}).get("text", "").strip()
        return "Не удалось получить текст ответа от YandexGPT."

    def _build_local_fallback_answer(self, query: str, chunks: List[Dict[str, Any]]) -> str:
        """
        Резервный обработчик на базе извлеченных чанков (RAG).
        Формирует четкий ответ прямо по найденным статьям документации.
        """
        if not chunks:
            return (
                "К сожалению, в базе знаний документации API HH.ru не нашлось информации "
                "по вашему вопросу. Попробуйте переформулировать запрос или обратитесь "
                "к официальному репозиторию https://github.com/hhru/api"
            )

        top_chunk = chunks[0]
        # Выбираем краткий фрагмент текста до 800 символов
        snippet = top_chunk["text"].strip()
        if len(snippet) > 800:
            snippet = snippet[:800].rsplit("\n", 1)[0] + "\n..."

        answer = (
            f"По вашему запросу найдена информация в разделе **{top_chunk['title']}** "
            f"({top_chunk['filename']}):\n\n"
            f"{snippet}"
        )
        return answer
