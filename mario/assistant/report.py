"""
Riepilogo del giorno per i genitori, dal diario dell'assistente (tabelle `assistant_messages` e
`assistant_memory` del database della dashboard).

`day_summary()` conta in locale: tempo di gioco stimato (somma delle pause sotto i cinque minuti),
partite, messaggi del bambino (scritti o a voce), eventi dei sensori, risposte di Mario, emozioni,
messaggi e risposte bloccati, ricordi nuovi. `summary_request()` prepara, su richiesta esplicita, il
testo da mandare al modello per un riassunto scritto: la conversazione del giorno era già passata dal
modello durante il gioco, ma la pagina lo dice prima di inviarla.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Optional

GAP = 300.0          # secondi: una pausa più lunga separa due partite
CHILD_SOURCES = {"utente", "voce", "pagina", "dashboard", ""}
TRANSCRIPT_MAX = 12000
SUMMARY_TOKENS = 800  # le risposte di Mario ne hanno 350: troppo pochi per otto righe (riassunto troncato il 16/10)

SUMMARY_SYSTEM = ("Scrivi per i genitori un riassunto breve (da cinque a otto righe, in italiano semplice, senza "
                  "elenchi) della giornata di gioco del loro bambino con Mario, un giocattolo che parla attraverso "
                  "un modello di linguaggio. Racconta di cosa hanno parlato e giocato, che umore aveva il bambino e "
                  "cosa gli è piaciuto. Se c'è qualcosa da notare (paure, richieste insolite, dati personali, "
                  "messaggi bloccati) dillo con calma alla fine. Niente giudizi e niente consigli educativi.")


def is_child(message: dict) -> bool:
    return message.get("role") == "user" and (message.get("source") or "") in CHILD_SOURCES


def day_summary(messages: list[dict], memories: Optional[list[dict]] = None) -> dict:
    messages = sorted(messages, key=lambda m: m["ts"])
    child = [m for m in messages if is_child(m)]
    sensors = [m for m in messages if m.get("role") == "user" and not is_child(m)]
    answers = [m for m in messages if m.get("role") == "assistant"]
    blocked = [m for m in messages if m.get("role") == "system"]
    played, sessions, prev = 0.0, 0, None
    for m in messages:
        if prev is None or m["ts"] - prev > GAP:
            sessions += 1
        else:
            played += m["ts"] - prev
        prev = m["ts"]
    sensor_kinds = Counter((m.get("source") or "?").split(":", 1)[0].strip() for m in sensors)
    return {
        "messages": len(messages), "child": len(child), "voice": sum(1 for m in child if m.get("source") == "voce"),
        "typed": sum(1 for m in child if m.get("source") != "voce"), "sensors": len(sensors),
        "sensor_kinds": dict(sensor_kinds.most_common()), "answers": len(answers),
        "emotions": dict(Counter(m["emotion"] for m in answers if m.get("emotion")).most_common()),
        "blocked": [{"ts": m["ts"], "text": m["content"]} for m in blocked],
        "memories": [m["text"] for m in memories or []],
        "minutes": round(played / 60.0), "sessions": sessions,
        "first": messages[0]["ts"] if messages else None, "last": messages[-1]["ts"] if messages else None,
    }


def transcript(messages: list[dict], limit: int = TRANSCRIPT_MAX) -> str:
    """La conversazione del giorno in righe "ora chi: testo" (la parte finale, se è troppo lunga)."""
    lines = []
    for m in sorted(messages, key=lambda m: m["ts"]):
        role = m.get("role")
        who = ("Mario" if role == "assistant" else "Sicurezza" if role == "system"
               else "Bambino" + (" (a voce)" if m.get("source") == "voce" else "") if is_child(m)
               else f"Sensore ({m.get('source')})")
        lines.append(f"{time.strftime('%H:%M', time.localtime(m['ts']))} {who}: {m['content']}")
    text = "\n".join(lines)
    return text[-limit:] if len(text) > limit else text


def summary_request(messages: list[dict], summary: dict, day: str) -> list[dict]:
    """Il messaggio per il modello: numeri del giorno e conversazione."""
    facts = (f"Giorno: {day}. Tempo di gioco stimato: {summary['minutes']} minuti in {summary['sessions']} partite. "
             f"Messaggi del bambino: {summary['child']} ({summary['voice']} a voce). Eventi dei sensori: "
             f"{summary['sensors']}. Messaggi bloccati: {len(summary['blocked'])}. Ricordi nuovi: "
             + ("; ".join(summary["memories"]) or "nessuno") + ".")
    return [{"role": "user", "content": f"{facts}\n\nConversazione:\n{transcript(messages)}"}]
