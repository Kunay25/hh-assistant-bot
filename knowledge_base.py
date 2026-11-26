"""Автоматически обновляемая локальная база знаний по документации HH.ru с гибридным поиском Qdrant."""
import atexit
import hashlib
import json
import logging
import os
import re
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import DOCS_DIR, HH_API_GITHUB_COMMITS_URL, HH_API_GITHUB_CONTENTS_URL, QDRANT_DIR

logger = logging.getLogger(__name__)

DENSE_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
SPARSE_MODEL_NAME = "Qdrant/bm25"
COLLECTION_NAME = "hh_docs_hybrid"
DENSE_VECTOR_SIZE = 384

_MODEL_LOCK = threading.Lock()
_SHARED_DENSE_MODEL = None
_SHARED_SPARSE_MODEL = None
_QDRANT_CLIENTS: Dict[str, Any] = {}


def _get_embedding_models():
    """Лениво загружает модели эмбеддингов один раз на весь процесс."""
    global _SHARED_DENSE_MODEL, _SHARED_SPARSE_MODEL
    with _MODEL_LOCK:
        if _SHARED_DENSE_MODEL is None or _SHARED_SPARSE_MODEL is None:
            from fastembed import SparseTextEmbedding, TextEmbedding

            _SHARED_DENSE_MODEL = TextEmbedding(model_name=DENSE_MODEL_NAME)
            _SHARED_SPARSE_MODEL = SparseTextEmbedding(model_name=SPARSE_MODEL_NAME, language="russian")
        return _SHARED_DENSE_MODEL, _SHARED_SPARSE_MODEL


def _get_qdrant_client(qdrant_dir: Path):
    """Возвращает общий экземпляр локального клиента Qdrant для указанного каталога."""
    from qdrant_client import QdrantClient

    key = str(qdrant_dir.resolve())
    with _MODEL_LOCK:
        if key not in _QDRANT_CLIENTS:
            qdrant_dir.mkdir(parents=True, exist_ok=True)
            _QDRANT_CLIENTS[key] = QdrantClient(path=key)
        return _QDRANT_CLIENTS[key]


def _close_qdrant_clients() -> None:
    """Корректно закрывает локальные клиенты Qdrant до завершения интерпретатора."""
    with _MODEL_LOCK:
        for client in _QDRANT_CLIENTS.values():
            try:
                client.close()
            except Exception:
                pass
        _QDRANT_CLIENTS.clear()


atexit.register(_close_qdrant_clients)


class KnowledgeBase:
    def __init__(
        self,
        docs_dir: Path = DOCS_DIR,
        qdrant_dir: Optional[Path] = None,
        use_vector_search: bool = True,
    ):
        self.docs_dir = Path(docs_dir)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        if qdrant_dir is not None:
            self.qdrant_dir = Path(qdrant_dir)
        elif self.docs_dir.resolve() == Path(DOCS_DIR).resolve():
            self.qdrant_dir = Path(QDRANT_DIR)
        else:
            self.qdrant_dir = self.docs_dir.parent / "qdrant_db"

        self.metadata_path = self.docs_dir / "kb_metadata.json"
        self.use_vector_search = use_vector_search
        self.chunks: List[Dict[str, Any]] = []
        self._signature: Tuple = ()
        self._indexed_signature: Optional[Tuple] = None
        self._hybrid_ready = False
        self._lock = threading.RLock()
        self._update_lock = threading.Lock()
        self._update_thread = None

        self._load_local_docs()

    def _get_signature(self) -> Tuple:
        """По изменениям на диске понимаем, что индекс нужно перечитать."""
        return tuple(
            sorted(
                (p.name, p.stat().st_mtime_ns, p.stat().st_size)
                for p in self.docs_dir.glob("*.md")
            )
        )

    def _signature_hash(self, signature: Tuple) -> str:
        raw = json.dumps(signature, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def _load_metadata(self) -> Dict[str, Any]:
        if not self.metadata_path.exists():
            return {}
        try:
            return json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_metadata(self, metadata: Dict[str, Any]) -> None:
        tmp = self.metadata_path.with_name(self.metadata_path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.metadata_path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def _fetch_docs_commit_info(self, headers: Dict[str, str], remote_files: Dict[str, str]) -> Tuple[str, str]:
        """Получает хэш и дату последнего коммита в папке docs на GitHub."""
        try:
            req = urllib.request.Request(HH_API_GITHUB_COMMITS_URL, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                commits = json.load(response)
            if isinstance(commits, list) and commits:
                commit = commits[0]
                sha = str(commit.get("sha", ""))[:7]
                commit_date = (
                    commit.get("commit", {})
                    .get("committer", {})
                    .get("date", "")
                )
                if sha:
                    return sha, commit_date
        except Exception as exc:
            logger.debug("Не удалось получить коммит docs с GitHub: %s", exc)

        # Запасной вариант версии: хэш от списка файлов и их SHA на GitHub.
        digest_src = json.dumps(sorted(remote_files.items())).encode("utf-8")
        return hashlib.sha256(digest_src).hexdigest()[:7], ""

    def _load_local_docs(self) -> None:
        chunks = []
        for path in sorted(self.docs_dir.glob("*.md")):
            try:
                chunks.extend(self._split_markdown(path.name, path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError) as exc:
                logger.warning("Невозможно прочитать %s: %s", path.name, exc)
        with self._lock:
            self.chunks = chunks
            self._signature = self._get_signature()
            self._indexed_signature = None

        if self.use_vector_search and self.chunks:
            self._sync_qdrant_index()

    def _refresh_if_changed(self) -> None:
        signature = self._get_signature()
        with self._lock:
            changed = signature != self._signature
        if changed:
            self._load_local_docs()
        elif self.use_vector_search and self.chunks and self._indexed_signature != signature:
            self._sync_qdrant_index()

    def _sync_qdrant_index(self) -> None:
        """Создает или обновляет гибридный индекс (Dense + Russian BM25) в локальном Qdrant."""
        with self._lock:
            chunks_snapshot = list(self.chunks)
            sig = self._signature
            if not chunks_snapshot:
                self._hybrid_ready = False
                return
            if self._indexed_signature == sig and self._hybrid_ready:
                return

        try:
            from qdrant_client import models

            client = _get_qdrant_client(self.qdrant_dir)
            dense_model, sparse_model = _get_embedding_models()

            state_file = self.qdrant_dir / "index_state.json"
            expected_state = {
                "signature": self._signature_hash(sig),
                "dense_model": DENSE_MODEL_NAME,
                "sparse_model": SPARSE_MODEL_NAME,
                "chunks_count": len(chunks_snapshot),
            }

            collection_exists = client.collection_exists(COLLECTION_NAME)
            if collection_exists and state_file.exists():
                try:
                    saved_state = json.loads(state_file.read_text(encoding="utf-8"))
                    if saved_state == expected_state:
                        with self._lock:
                            self._indexed_signature = sig
                            self._hybrid_ready = True
                        return
                except (OSError, ValueError):
                    pass

            if collection_exists:
                client.delete_collection(COLLECTION_NAME)

            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config={
                    "dense": models.VectorParams(
                        size=DENSE_VECTOR_SIZE,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
                },
            )

            texts = [f"{c['title']} ({c['filename']})\n{c['text']}" for c in chunks_snapshot]
            dense_vectors = list(dense_model.embed(texts))
            sparse_vectors = list(sparse_model.embed(texts))

            points = [
                models.PointStruct(
                    id=idx,
                    vector={
                        "dense": dense_vectors[idx].tolist(),
                        "sparse": models.SparseVector(
                            indices=sparse_vectors[idx].indices.tolist(),
                            values=sparse_vectors[idx].values.tolist(),
                        ),
                    },
                    payload=chunks_snapshot[idx],
                )
                for idx in range(len(chunks_snapshot))
            ]

            with _MODEL_LOCK:
                client.upsert(collection_name=COLLECTION_NAME, points=points)

            state_file.write_text(json.dumps(expected_state, ensure_ascii=False, indent=2), encoding="utf-8")
            with self._lock:
                self._indexed_signature = sig
                self._hybrid_ready = True
        except Exception as exc:
            logger.warning("Гибридный индекс Qdrant недоступен (%s). Используется резервный поиск.", exc)
            with self._lock:
                self._hybrid_ready = False

    def download_docs(self) -> Dict[str, Any]:
        """Обновляет Markdown-файлы, удаляет устаревшие и сохраняет версию документации."""
        if not self._update_lock.acquire(blocking=False):
            return {"success": False, "downloaded": 0, "deleted": 0, "error": "Обновление уже выполняется"}
        try:
            headers = {"User-Agent": "HH-Assistant-Bot/1.2", "Accept": "application/vnd.github+json"}
            request = urllib.request.Request(HH_API_GITHUB_CONTENTS_URL, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    items = json.load(response)
                if not isinstance(items, list):
                    raise ValueError("GitHub API вернул неожиданный формат списка")
            except Exception as exc:
                logger.warning("Документация GitHub недоступна: %s", exc)
                return {
                    "success": False,
                    "downloaded": 0,
                    "deleted": 0,
                    "error": str(exc),
                    "local_files": len(list(self.docs_dir.glob("*.md"))),
                }

            remote_files: Dict[str, str] = {}
            valid_items: List[Tuple[str, str, str]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "")
                url = item.get("download_url")
                sha = str(item.get("sha", ""))
                # Не позволяем ответу API записывать файлы вне каталога документов.
                if not isinstance(name, str) or not name.endswith(".md") or Path(name).name != name:
                    continue
                if not isinstance(url, str) or not url.startswith("https://raw.githubusercontent.com/"):
                    continue
                remote_files[name] = sha
                valid_items.append((name, url, sha))

            if not remote_files:
                return {
                    "success": False,
                    "downloaded": 0,
                    "deleted": 0,
                    "error": "В ответе GitHub не найдено ни одного .md файла",
                }

            downloaded = 0
            errors = 0
            for name, url, _ in valid_items:
                path = self.docs_dir / name
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
                        content = response.read().decode("utf-8")
                    if path.exists() and path.read_text(encoding="utf-8") == content:
                        continue
                    temporary = path.with_name(path.name + ".tmp")
                    try:
                        temporary.write_text(content, encoding="utf-8")
                        os.replace(temporary, path)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
                    downloaded += 1
                except Exception as exc:
                    errors += 1
                    logger.warning("Не удалось обновить %s: %s", name, exc)

            # Удаляем локальные файлы, которых больше нет в исходном репозитории (только если список получен без ошибок)
            deleted = 0
            if errors == 0:
                for local_path in list(self.docs_dir.glob("*.md")):
                    if local_path.name not in remote_files:
                        try:
                            local_path.unlink()
                            deleted += 1
                            logger.info("Удален устаревший документ: %s", local_path.name)
                        except OSError as exc:
                            logger.warning("Не удалось удалить %s: %s", local_path.name, exc)

            version, commit_date = self._fetch_docs_commit_info(headers, remote_files)
            updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            metadata = {
                "updated_at": updated_at,
                "docs_version": version,
                "commit_date": commit_date,
                "files_count": len(remote_files),
                "files": remote_files,
            }
            self._save_metadata(metadata)

            self._load_local_docs()
            stats = self.get_stats()
            return {
                "success": errors == 0 and stats["files_count"] > 0,
                "downloaded": downloaded,
                "deleted": deleted,
                "errors": errors,
                "total_chunks": stats["chunks_count"],
                "files_count": stats["files_count"],
                "docs_version": version,
                "updated_at": updated_at,
            }
        finally:
            self._update_lock.release()

    def start_auto_updates(self, interval_hours: float = 6.0) -> None:
        """Запускается один раз из main.py; не мешает работе MAX и Telegram."""
        if interval_hours <= 0:
            raise ValueError("Интервал обновления должен быть положительным")
        with self._lock:
            if self._update_thread and self._update_thread.is_alive():
                return

            def worker() -> None:
                while True:
                    try:
                        result = self.download_docs()
                        if result.get("success"):
                            print(
                                f"[KB] Обновление: изменено {result['downloaded']}, "
                                f"удалено {result.get('deleted', 0)}, версия {result.get('docs_version')}"
                            )
                        else:
                            logger.warning("Обновление не выполнено; сохранены предыдущие документы")
                    except Exception:
                        logger.exception("Неожиданная ошибка фонового обновления")
                    threading.Event().wait(interval_hours * 3600)

            self._update_thread = threading.Thread(
                target=worker, name="hh-docs-auto-update", daemon=True
            )
            self._update_thread.start()

    def _split_markdown(self, filename: str, source: str) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        url = f"https://github.com/hhru/api/blob/master/docs/{filename}"
        # Убираем HTML-якори <a name="..."></a>, чтобы они не засоряли эмбеддинги.
        cleaned = re.sub(r"<a\s+name=[\"'][^\"']*[\"']\s*>\s*</a>\s*", "", source)

        raw_sections = [s.strip() for s in re.split(r"\n(?=#{1,2}\s+)", cleaned) if s.strip()]

        # Объединяем слишком короткие соседние разделы одного файла в цельные смысловые блоки.
        merged_sections: List[str] = []
        for section in raw_sections:
            if merged_sections and len(merged_sections[-1]) < 350 and len(merged_sections[-1]) + len(section) <= 1300:
                merged_sections[-1] = merged_sections[-1] + "\n\n" + section
            else:
                merged_sections.append(section)

        for section in merged_sections:
            first = section.split("\n", 1)[0]
            title = first.lstrip("#").strip() if first.startswith("#") else filename
            if len(section) <= 1500:
                pieces = [section]
            else:
                pieces = []
                current = ""
                for paragraph in section.split("\n\n"):
                    if current and len(current) + len(paragraph) > 1400:
                        pieces.append(current)
                        current = ""
                    current += ("\n\n" if current else "") + paragraph
                if current:
                    pieces.append(current)
            for text in pieces:
                chunks.append({"filename": filename, "title": title, "text": text, "url": url})
        return chunks

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_]{3,}", text.lower())

    def _keyword_fallback_search(self, query: str, candidates: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        words = set(self._tokenize(query))
        if not words:
            return candidates[:top_k]
        scored = []
        for chunk in candidates:
            title_tokens = set(self._tokenize(chunk["title"]))
            filename_tokens = set(self._tokenize(chunk["filename"]))
            text_tokens = self._tokenize(chunk["text"])
            score = 5 * len(words & title_tokens) + 4 * len(words & filename_tokens)
            score += sum(
                1 + min(text_tokens.count(word), 5) * 0.5
                for word in words
                if word in text_tokens
            )
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [chunk for _, chunk in scored[:top_k]]

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Гибридный поиск (смысловые эмбеддинги + русский BM25 + RRF) через Qdrant."""
        self._refresh_if_changed()
        with self._lock:
            candidates = list(self.chunks)
            hybrid_ready = self._hybrid_ready
        if not candidates or top_k <= 0:
            return []
        if not query.strip():
            return candidates[:top_k]

        if self.use_vector_search and hybrid_ready:
            try:
                from qdrant_client import models

                client = _get_qdrant_client(self.qdrant_dir)
                dense_model, sparse_model = _get_embedding_models()

                query_dense = next(iter(dense_model.embed([query]))).tolist()
                query_sparse = next(iter(sparse_model.embed([query])))

                prefetch_limit = max(10, top_k * 4)
                with _MODEL_LOCK:
                    result = client.query_points(
                        collection_name=COLLECTION_NAME,
                        prefetch=[
                            models.Prefetch(
                                query=query_dense,
                                using="dense",
                                limit=prefetch_limit,
                            ),
                            models.Prefetch(
                                query=models.SparseVector(
                                    indices=query_sparse.indices.tolist(),
                                    values=query_sparse.values.tolist(),
                                ),
                                using="sparse",
                                limit=prefetch_limit,
                            ),
                        ],
                        query=models.FusionQuery(fusion=models.Fusion.RRF),
                        limit=top_k,
                    )
                if result.points:
                    return [point.payload for point in result.points if point.payload]
            except Exception as exc:
                logger.warning("Ошибка гибридного поиска Qdrant (%s), откат на ключевые слова.", exc)

        return self._keyword_fallback_search(query, candidates, top_k)

    def get_stats(self) -> Dict[str, Any]:
        self._refresh_if_changed()
        with self._lock:
            count = len(self.chunks)
            hybrid = self._hybrid_ready
        files = sorted(p.name for p in self.docs_dir.glob("*.md"))
        meta = self._load_metadata()
        return {
            "files_count": len(files),
            "chunks_count": count,
            "files": files,
            "docs_version": meta.get("docs_version", "не определена"),
            "updated_at": meta.get("updated_at", "не обновлялась"),
            "commit_date": meta.get("commit_date", ""),
            "search_mode": "гибридный (Qdrant: Dense + Russian BM25 + RRF)" if hybrid else "по ключевым словам",
        }
