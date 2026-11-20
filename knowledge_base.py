import os
import re
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Any, Tuple
from config import DOCS_DIR, HH_API_GITHUB_CONTENTS_URL

class KnowledgeBase:
    def __init__(self, docs_dir: Path = DOCS_DIR):
        self.docs_dir = Path(docs_dir)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.chunks: List[Dict[str, Any]] = []
        self._load_local_docs()

    def download_docs(self) -> Dict[str, Any]:
        """
        Скачивает или обновляет документацию API HH.ru напрямую из репозитория GitHub.
        """
        downloaded = 0
        errors = 0
        
        headers = {"User-Agent": "HH-Assistant-Bot/1.0"}
        req = urllib.request.Request(HH_API_GITHUB_CONTENTS_URL, headers=headers)
        
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                items = json.loads(response.read().decode("utf-8"))
        except Exception as e:
            print(f"[KnowledgeBase] Ошибка при запросе к GitHub API: {e}")
            return {
                "success": False,
                "downloaded": 0,
                "error": str(e),
                "local_files": len(list(self.docs_dir.glob("*.md")))
            }

        for item in items:
            name = item.get("name", "")
            download_url = item.get("download_url")
            
            if name.endswith(".md") and download_url:
                target_file = self.docs_dir / name
                try:
                    file_req = urllib.request.Request(download_url, headers=headers)
                    with urllib.request.urlopen(file_req, timeout=10) as f_res:
                        content = f_res.read().decode("utf-8")
                        with open(target_file, "w", encoding="utf-8") as out:
                            out.write(content)
                        downloaded += 1
                except Exception as file_err:
                    print(f"[KnowledgeBase] Ошибка скачивания {name}: {file_err}")
                    errors += 1

        self._load_local_docs()
        return {
            "success": True,
            "downloaded": downloaded,
            "errors": errors,
            "total_chunks": len(self.chunks)
        }

    def _load_local_docs(self) -> None:
        """
        Считывает сохраненные файлы Markdown и разбивает их на смысловые фрагменты (чанки).
        """
        self.chunks = []
        md_files = list(self.docs_dir.glob("*.md"))
        
        for file_path in md_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                file_chunks = self._split_markdown(file_path.name, content)
                self.chunks.extend(file_chunks)
            except Exception as e:
                print(f"[KnowledgeBase] Не удалось прочитать {file_path.name}: {e}")

    def _split_markdown(self, filename: str, text: str) -> List[Dict[str, Any]]:
        """
        Разбивает текст markdown-файла по заголовкам (# и ##).
        """
        chunks = []
        sections = re.split(r'\n(?=#{1,3}\s+)', text)
        github_url = f"https://github.com/hhru/api/blob/master/docs/{filename}"
        
        for section in sections:
            section = section.strip()
            if not section:
                continue
            
            first_line = section.split("\n", 1)[0]
            header_title = first_line.lstrip("#").strip() if first_line.startswith("#") else filename
            
            # Если фрагмент слишком длинный, режем на абзацы по ~1200 символов
            if len(section) > 1500:
                paragraphs = section.split("\n\n")
                current_buf = []
                current_len = 0
                
                for p in paragraphs:
                    current_buf.append(p)
                    current_len += len(p)
                    if current_len >= 1200:
                        part_text = "\n\n".join(current_buf).strip()
                        chunks.append({
                            "filename": filename,
                            "title": header_title,
                            "text": part_text,
                            "url": github_url
                        })
                        current_buf = []
                        current_len = 0
                
                if current_buf:
                    part_text = "\n\n".join(current_buf).strip()
                    chunks.append({
                        "filename": filename,
                        "title": header_title,
                        "text": part_text,
                        "url": github_url
                    })
            else:
                chunks.append({
                    "filename": filename,
                    "title": header_title,
                    "text": section,
                    "url": github_url
                })
                
        return chunks

    def _tokenize(self, text: str) -> List[str]:
        words = re.findall(r'[a-zA-Zа-яА-Я0-9_]{3,}', text.lower())
        return words

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Находит самые релевантные фрагменты документации по тексту запроса.
        """
        if not self.chunks:
            self._load_local_docs()
            
        if not self.chunks:
            return []

        query_tokens = set(self._tokenize(query))
        if not query_tokens:
            return self.chunks[:top_k]

        scored_chunks: List[Tuple[float, Dict[str, Any]]] = []
        
        for chunk in self.chunks:
            score = 0.0
            title_tokens = set(self._tokenize(chunk["title"]))
            file_tokens = set(self._tokenize(chunk["filename"]))
            text_tokens = self._tokenize(chunk["text"])
            
            # Совпадения с заголовком и именем файла имеют больший вес
            title_matches = len(query_tokens.intersection(title_tokens))
            score += title_matches * 5.0

            file_matches = len(query_tokens.intersection(file_tokens))
            score += file_matches * 4.0

            # Частота вхождений слов в текст
            for token in query_tokens:
                count = text_tokens.count(token)
                if count > 0:
                    score += 1.0 + min(count, 5) * 0.5
            
            if score > 0:
                scored_chunks.append((score, chunk))

        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        return [chunk for _, chunk in scored_chunks[:top_k]]

    def get_stats(self) -> Dict[str, Any]:
        md_files = list(self.docs_dir.glob("*.md"))
        return {
            "files_count": len(md_files),
            "chunks_count": len(self.chunks),
            "files": [f.name for f in md_files]
        }
