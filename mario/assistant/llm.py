"""
Backend per il modello di linguaggio, con risposta in streaming (pezzi di testo).

  anthropic  API Messages di Claude (https://api.anthropic.com), chiave in ANTHROPIC_API_KEY
             o nelle impostazioni della dashboard. Modello predefinito: claude-sonnet-5-5.
  openai     qualsiasi endpoint compatibile "chat/completions" (Ollama, LM Studio, OpenAI):
             base URL + modello, chiave facoltativa.
  demo       risposte locali a regole, senza rete: serve per provare tutta la catena
             sensori -> prompt -> emozioni -> voce anche senza una chiave.

Solo libreria standard (urllib): niente dipendenze in più. Ogni backend espone
`stream(system, messages)` che restituisce un iteratore di stringhe; chiuderlo interrompe
la richiesta.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Iterator, Optional

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
ANTHROPIC_MODELS = ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5", "claude-fable-5-1"]


class LLMError(Exception):
    pass


@dataclass
class LLMConfig:
    backend: str = "auto"          # auto / anthropic / openai / demo
    model: str = ""
    api_key: str = ""
    base_url: str = ""             # solo openai: es. http://localhost:11434/v1
    max_tokens: int = 350
    temperature: float = 0.8       # usata solo dagli endpoint compatibili OpenAI
    timeout: float = 60.0
    demo_delay: float = 0.04
    extra: dict = field(default_factory=dict)

    def resolved_backend(self) -> str:
        if self.backend and self.backend != "auto":
            return self.backend
        if self.api_key or os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if self.base_url or os.environ.get("OPENAI_BASE_URL"):
            return "openai"
        return "demo"

    def to_dict(self, mask_key: bool = True) -> dict:
        d = asdict(self)
        if mask_key:
            d["api_key"] = mask(self.api_key)
        d["resolved_backend"] = self.resolved_backend()
        return d


def mask(secret: str) -> str:
    if not secret:
        return ""
    return secret[:6] + "…" + secret[-4:] if len(secret) > 12 else "•" * len(secret)


class Backend:
    name = "base"
    model = ""

    def stream(self, system: str, messages: list[dict]) -> Iterator[str]:  # pragma: no cover - interfaccia
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.name} ({self.model})" if self.model else self.name


def _post_sse(url: str, headers: dict, body: dict, timeout: float) -> Iterator[str]:
    """POST JSON e restituisce i campi `data:` degli eventi SSE, uno per volta."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream", **headers},
                                 method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        try:
            parsed = json.loads(detail)
            detail = parsed.get("error", {}).get("message") or parsed.get("message") or detail
        except (ValueError, AttributeError):
            pass
        raise LLMError(f"HTTP {exc.code} da {url}: {detail}") from None
    except urllib.error.URLError as exc:
        raise LLMError(f"rete non raggiungibile ({exc.reason})") from None
    except OSError as exc:
        raise LLMError(f"errore di rete: {exc}") from None
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("data:"):
                yield line[5:].strip()


class AnthropicBackend(Backend):
    name = "anthropic"

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.api_key = cfg.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = cfg.model or DEFAULT_ANTHROPIC_MODEL
        if not self.api_key:
            raise LLMError("manca la chiave API di Claude (ANTHROPIC_API_KEY o impostazioni dell'assistente)")

    def stream(self, system: str, messages: list[dict]) -> Iterator[str]:
        # niente `temperature`: i modelli Claude 5 la rifiutano ("deprecated for this model", HTTP 400)
        body = {"model": self.model, "max_tokens": int(self.cfg.max_tokens), "system": system, "stream": True,
                "messages": [{"role": m["role"], "content": m["content"]} for m in messages]}
        headers = {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION}
        for data in _post_sse(ANTHROPIC_URL, headers, body, self.cfg.timeout):
            if not data:
                continue
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            etype = ev.get("type")
            if etype == "content_block_delta":
                delta = ev.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield delta["text"]
            elif etype == "error":
                err = ev.get("error") or {}
                raise LLMError(f"{err.get('type', 'errore')}: {err.get('message', data)}")
            elif etype == "message_stop":
                return


class OpenAICompatBackend(Backend):
    name = "openai"

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.base_url = (cfg.base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = cfg.model or os.environ.get("OPENAI_MODEL", "")
        if not self.model:
            raise LLMError("indica il nome del modello per l'endpoint compatibile OpenAI (es. llama3.2 per Ollama)")

    def stream(self, system: str, messages: list[dict]) -> Iterator[str]:
        body = {"model": self.model, "stream": True, "temperature": float(self.cfg.temperature),
                "max_tokens": int(self.cfg.max_tokens),
                "messages": [{"role": "system", "content": system}] + [{"role": m["role"], "content": m["content"]} for m in messages]}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        for data in _post_sse(self.base_url + "/chat/completions", headers, body, self.cfg.timeout):
            if not data or data == "[DONE]":
                if data == "[DONE]":
                    return
                continue
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            if ev.get("error"):
                err = ev["error"]
                raise LLMError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
            for choice in ev.get("choices") or []:
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text


# Risposte locali: (parole chiave nell'ultimo messaggio, risposta con tag emozione)
DEMO_RULES: list[tuple[tuple[str, ...], str]] = [
    (("salto", "saltato"), "[gioia] Yahoo! Che salto fantastico! [calma] Atterraggio perfetto, come sempre."),
    (("caduto", "caduta", "botto"), "[allarme] Ahia! Mamma mia, che capitombolo! [tristezza] Mi gira un po' la testa. [calma] Ma un idraulico non si arrende mai."),
    (("girare", "rotazione"), "[sorpresa] Uooo, gira tutto! [gioia] Ancora, ancora!"),
    (("pantaloni", "indosso"), "[sorpresa] Oh! Pantaloni nuovi! [gioia] Mi sento già diverso, guardate come sto bene!"),
    (("start", "si parte", "partenza"), "[gioia] Si parte! Let's-a go! [calma] Pronti per una nuova avventura."),
    (("traguardo", "bandiera"), "[gioia] Traguardo! Ce l'abbiamo fatta! [sorpresa] Guarda quante monete!"),
    (("goomba", "nemico", "bowser", "boo", "koopa", "piranha", "pokey", "thwomp", "twomp"),
     "[allarme] Attenzione, un nemico! [gioia] Ci penso io: un bel salto sulla testa e via!"),
    (("fungo", "stella", "tesoro", "regalo", "premio", "forziere", "blocco ?"),
     "[sorpresa] Oh, un premio! [gioia] Evviva, proprio quello che mi serviva!"),
    (("lava", "scotta", "fuoco"), "[allarme] Ahi ahi, scotta! [calma] Meglio saltare via subito."),
    (("acqua", "splash"), "[gioia] Splash! Che bella nuotata."),
    (("ghiaccio", "scivol"), "[sorpresa] Uiii, si scivola! [gioia] Che divertimento!"),
    (("veleno",), "[allarme] Veleno! Via di qui, presto!"),
    (("batteria", "stanco", "scarica"), "[sonno] Uaaah, che sonno. [calma] Mi servirebbe una ricarica, o un bel pisolino."),
    (("moneta", "monete"), "[gioia] Din din! Un'altra moneta nel salvadanaio!"),
    (("chi sei", "come ti chiami", "presentati"), "[gioia] It's-a me, Mario! [calma] Un idraulico LEGO con un sacco di sensori addosso."),
    (("ciao", "buongiorno", "buonasera", "salve", "hey"), "[gioia] Ciao! Sono Mario, piacere di conoscerti! [calma] Vuoi giocare con me?"),
    (("storia", "racconta", "favola"), "[pensieroso] Mmm, vediamo. [gioia] C'era una volta un tubo verde che portava in un regno pieno di funghi! [sorpresa] E indovina chi ci è saltato dentro?"),
    (("grazie",), "[gioia] Prego, è stato un piacere! [calma] Quando vuoi, sono qui."),
    (("triste", "piang"), "[tristezza] Oh no, mi dispiace. [calma] Vieni, facciamo un salto insieme e passa tutto."),
]
DEMO_DEFAULT = ("[pensieroso] Mmm, bella domanda. [calma] Sono la versione dimostrativa senza modello: "
                "per risposte vere inserisci una chiave API di Claude nelle impostazioni dell'assistente.")


class DemoBackend(Backend):
    name = "demo"
    model = "regole locali"

    def __init__(self, cfg: Optional[LLMConfig] = None):
        self.delay = cfg.demo_delay if cfg else 0.0

    def answer(self, text: str) -> str:
        low = text.lower()
        for keys, reply in DEMO_RULES:
            if any(k in low for k in keys):
                return reply
        return DEMO_DEFAULT

    def stream(self, system: str, messages: list[dict]) -> Iterator[str]:
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        words = self.answer(last).split(" ")
        for i, word in enumerate(words):
            yield word + (" " if i < len(words) - 1 else "")
            if self.delay:
                time.sleep(self.delay)


def make_backend(cfg: LLMConfig) -> Backend:
    kind = cfg.resolved_backend()
    if kind == "anthropic":
        return AnthropicBackend(cfg)
    if kind == "openai":
        return OpenAICompatBackend(cfg)
    if kind == "demo":
        return DemoBackend(cfg)
    raise LLMError(f"backend sconosciuto: {cfg.backend}")
