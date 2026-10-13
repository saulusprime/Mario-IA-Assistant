"""
Memoria tra le sessioni: frasi brevi che Mario "ricorda" (nome di chi gioca, preferenze).

Il modello le produce con il tag `[ricorda: ...]` nella risposta (vedi `emotions.EmotionStream`),
oppure le scrive l'utente dalla pagina o dalla CLI. Finiscono nel prompt di sistema delle sessioni
successive. Due archivi: in memoria (test, `--no-mario` senza database) e nel database SQLite della
dashboard (tabella `assistant_memory`), condiviso da CLI e pagina Assistente.
"""
from __future__ import annotations

import time
from typing import Optional, Protocol


class MemoryStore(Protocol):
    def list(self, limit: int = 50) -> list[dict]: ...
    def add(self, text: str) -> int: ...
    def delete(self, memory_id: int) -> int: ...
    def clear(self) -> int: ...


def clean(text: str) -> str:
    return " ".join(str(text).split()).strip(" .;")[:200]


class ListMemory:
    """Archivio in RAM (dura quanto il processo)."""

    def __init__(self, items: Optional[list[str]] = None):
        self._items: list[dict] = []
        self._next = 1
        for text in items or []:
            self.add(text)

    def list(self, limit: int = 50) -> list[dict]:
        return list(self._items[-limit:])

    def add(self, text: str) -> int:
        text = clean(text)
        if not text:
            return 0
        for item in self._items:
            if item["text"].lower() == text.lower():
                return item["id"]
        item = {"id": self._next, "ts": time.time(), "text": text}
        self._items.append(item)
        self._next += 1
        return item["id"]

    def delete(self, memory_id: int) -> int:
        before = len(self._items)
        self._items = [i for i in self._items if i["id"] != memory_id]
        return before - len(self._items)

    def clear(self) -> int:
        n = len(self._items)
        self._items.clear()
        return n


class DatabaseMemory:
    """Archivio nella tabella `assistant_memory` del database della dashboard (mario.web.db.Database)."""

    def __init__(self, db):
        self.db = db

    def list(self, limit: int = 50) -> list[dict]:
        return self.db.list_memories(limit)

    def add(self, text: str) -> int:
        text = clean(text)
        return self.db.add_memory(text) if text else 0

    def delete(self, memory_id: int) -> int:
        return self.db.delete_memory(memory_id)

    def clear(self) -> int:
        return self.db.clear_memories()


def memory_prompt(items: list[dict], limit: int = 20) -> str:
    """Blocco per il prompt di sistema; vuoto se non c'è nulla da ricordare."""
    texts = [i["text"] for i in items[-limit:] if i.get("text")]
    if not texts:
        return ""
    return "Cose che ricordi dalle volte scorse (usale con naturalezza, senza elencarle): " + "; ".join(texts) + "."
