"""Автоматически обновляемая локальная база знаний по документации HH.ru."""
import json
import logging
import os
import re
import threading
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config import DOCS_DIR, HH_API_GITHUB_CONTENTS_URL

logger = logging.getLogger(__name__)


class KnowledgeBase:
    def __init__(self, docs_dir: Path = DOCS_DIR):
        self.docs_dir = Path(docs_dir)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.chunks: List[Dict[str, Any]] = []
        self._signature: Tuple = ()
        self._lock = threading.RLock()
        self._update_lock = threading.Lock()
        self._update_thread = None
        self._load_local_docs()

    def _get_signature(self) -> Tuple:
        """По изменениям на диске понимаем, что индекс нужно перечитать."""
        return tuple(sorted(
            (p.name, p.stat().st_mtime_ns, p.stat().st_size)
            for p in self.docs_dir.glob('*.md')
        ))

    def _load_local_docs(self) -> None:
        chunks = []
        for path in sorted(self.docs_dir.glob('*.md')):
            try:
                chunks.extend(self._split_markdown(path.name, path.read_text(encoding='utf-8')))
            except (OSError, UnicodeError) as exc:
                logger.warning('Невозможно прочитать %s: %s', path.name, exc)
        with self._lock:
            self.chunks = chunks
            self._signature = self._get_signature()

    def _refresh_if_changed(self) -> None:
        signature = self._get_signature()
        with self._lock:
            changed = signature != self._signature
        if changed:
            self._load_local_docs()

    def download_docs(self) -> Dict[str, Any]:
        """Обновляет Markdown-файлы; при сбое старые документы сохраняются."""
        if not self._update_lock.acquire(blocking=False):
            return {'success': False, 'downloaded': 0, 'error': 'Обновление уже выполняется'}
        try:
            headers = {'User-Agent': 'HH-Assistant-Bot/1.1', 'Accept': 'application/vnd.github+json'}
            request = urllib.request.Request(HH_API_GITHUB_CONTENTS_URL, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    items = json.load(response)
                if not isinstance(items, list):
                    raise ValueError('GitHub API вернул неожиданный формат списка')
            except Exception as exc:
                logger.warning('Документация GitHub недоступна: %s', exc)
                return {'success': False, 'downloaded': 0, 'error': str(exc),
                        'local_files': len(list(self.docs_dir.glob('*.md')))}

            downloaded = 0
            errors = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = item.get('name', '')
                url = item.get('download_url')
                # Не позволяем ответу API записывать файлы вне каталога документов.
                if not isinstance(name, str) or not name.endswith('.md') or Path(name).name != name:
                    continue
                if not isinstance(url, str) or not url.startswith('https://raw.githubusercontent.com/hhru/api/'):
                    continue
                path = self.docs_dir / name
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
                        content = response.read().decode('utf-8')
                    if path.exists() and path.read_text(encoding='utf-8') == content:
                        continue
                    temporary = path.with_name(path.name + '.tmp')
                    try:
                        temporary.write_text(content, encoding='utf-8')
                        os.replace(temporary, path)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
                    downloaded += 1
                except Exception as exc:
                    errors += 1
                    logger.warning('Не удалось обновить %s: %s', name, exc)

            self._load_local_docs()
            stats = self.get_stats()
            return {'success': errors == 0 and stats['files_count'] > 0,
                    'downloaded': downloaded, 'errors': errors,
                    'total_chunks': stats['chunks_count'], 'files_count': stats['files_count']}
        finally:
            self._update_lock.release()

    def start_auto_updates(self, interval_hours: float = 6.0) -> None:
        """Запускается один раз из main.py; не мешает работе MAX и Telegram."""
        if interval_hours <= 0:
            raise ValueError('Интервал обновления должен быть положительным')
        with self._lock:
            if self._update_thread and self._update_thread.is_alive():
                return

            def worker() -> None:
                # Сначала проверяем свежесть документов, потом — через равные интервалы.
                while True:
                    try:
                        result = self.download_docs()
                        if result.get('success'):
                            print(f"[KB] Обновление документов: изменено {result['downloaded']}, всего {result['files_count']}")
                        else:
                            logger.warning('Обновление не выполнено; сохранены предыдущие документы')
                    except Exception:
                        logger.exception('Неожиданная ошибка фонового обновления')
                    threading.Event().wait(interval_hours * 3600)

            self._update_thread = threading.Thread(
                target=worker, name='hh-docs-auto-update', daemon=True
            )
            self._update_thread.start()

    def _split_markdown(self, filename: str, source: str) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        url = f'https://github.com/hhru/api/blob/master/docs/{filename}'
        for section in re.split(r'\n(?=#{1,3}\s+)', source):
            section = section.strip()
            if not section:
                continue
            first = section.split('\n', 1)[0]
            title = first.lstrip('#').strip() if first.startswith('#') else filename
            # Оставляем размер фрагментов умеренным для контекста языковой модели.
            if len(section) <= 1500:
                pieces = [section]
            else:
                pieces = []
                current = ''
                for paragraph in section.split('\n\n'):
                    if current and len(current) + len(paragraph) > 1400:
                        pieces.append(current)
                        current = ''
                    current += ('\n\n' if current else '') + paragraph
                if current:
                    pieces.append(current)
            for text in pieces:
                chunks.append({'filename': filename, 'title': title, 'text': text, 'url': url})
        return chunks

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9_]{3,}', text.lower())

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        # MAX и Telegram держат отдельные экземпляры KnowledgeBase: подхватываем
        # новые файлы на лету, даже если обновлял их экземпляр в main.py.
        self._refresh_if_changed()
        with self._lock:
            candidates = list(self.chunks)
        if not candidates or top_k <= 0:
            return []
        words = set(self._tokenize(query))
        if not words:
            return candidates[:top_k]
        scored = []
        for chunk in candidates:
            title_tokens = set(self._tokenize(chunk['title']))
            filename_tokens = set(self._tokenize(chunk['filename']))
            text_tokens = self._tokenize(chunk['text'])
            score = 5 * len(words & title_tokens) + 4 * len(words & filename_tokens)
            score += sum(1 + min(text_tokens.count(word), 5) * 0.5
                         for word in words if word in text_tokens)
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [chunk for _, chunk in scored[:top_k]]

    def get_stats(self) -> Dict[str, Any]:
        self._refresh_if_changed()
        with self._lock:
            count = len(self.chunks)
        files = sorted(p.name for p in self.docs_dir.glob('*.md'))
        return {'files_count': len(files), 'chunks_count': count, 'files': files}
