"""
Sessione: il ciclo "ingresso -> modello -> emozioni -> voce e comportamenti".

Una sessione tiene la cronologia della conversazione, lo stato del corpo di Mario (tramite
`IntentMapper`), l'emozione corrente e un solo turno alla volta. Un turno parte da un testo
scritto dall'utente o da un intento dei sensori; la risposta del modello arriva in streaming,
viene ripulita dai tag emozione, spezzata in frasi per la voce e accompagnata dai comportamenti
su Mario. Tutto quello che succede viene emesso come evento (`type` = ASSISTANT, campo `kind`)
per la dashboard (SSE) o la CLI.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Optional

from .behaviours import BehaviourEngine
from .emotions import DEFAULT_EMOTION, EMOTIONS, MODEL_EMOTIONS, EmotionStream, SentenceBuffer, strip_tags
from .intents import Intent, IntentMapper
from .llm import Backend, LLMError
from .tts import Speaker

BASE_PROMPT = """Sei Mario, il personaggio LEGO Super Mario: una statuina vera, con dei sensori, che parla attraverso il computer di casa. Parli italiano, in prima persona, con frasi brevi, allegre e adatte a bambini dai sei anni. Niente elenchi, niente markdown, niente emoji: solo frasi. Al massimo tre frasi, salvo che ti chiedano una storia.
Ogni frase inizia con un'emozione tra parentesi quadre, scelta solo tra: {tags}. Esempio: "[gioia] Yahoo, che salto! [calma] Adesso riposiamoci un momento."
I messaggi che iniziano con "[sensore: ...]" non li scrive una persona: arrivano dal tuo corpo (gesti, pantaloni, basette con il codice a barre, colore del terreno, pulsante, batteria). Reagisci come farebbe Mario nel gioco, in una o due frasi.
Le righe "(Stato del corpo: ...)" descrivono come sei in questo momento: tienine conto senza ripeterle.
{persona}
Stato del corpo adesso: {context}."""

FALLBACK_ERROR_SPEECH = "Ops, adesso non riesco a rispondere."


class AssistantBusy(Exception):
    pass


class AssistantSession:
    def __init__(self, backend_factory: Callable[[], Backend], speaker: Speaker, behaviours: BehaviourEngine,
                 emit: Optional[Callable[[dict], None]] = None, mapper: Optional[IntentMapper] = None,
                 persona: str = "", history_limit: int = 20,
                 on_message: Optional[Callable[[dict], None]] = None):
        self.backend_factory = backend_factory
        self.speaker = speaker
        self.behaviours = behaviours
        self.emit_cb = emit or (lambda ev: None)
        self.mapper = mapper or IntentMapper()
        self.persona = persona
        self.history_limit = history_limit
        self.on_message = on_message or (lambda msg: None)
        self.listening = True
        self.busy = False
        self.emotion = DEFAULT_EMOTION
        self.history: list[dict] = []
        self.last_answer = ""
        self.last_error = ""
        self.backend_name = ""
        self.turn = 0
        self.intents: deque = deque(maxlen=30)
        self._lock = threading.Lock()
        self._abort = False
        self._ctx_sent = ""
        self._thread: Optional[threading.Thread] = None
        self.pending: list[Intent] = []       # intenti arrivati mentre Mario parlava
        self.pending_ttl = 8.0                # secondi: più vecchi di così si scartano
        self.speaker.on_start = self._on_speaking
        self.speaker.on_end = self._on_spoken

    # ----- eventi in uscita -------------------------------------------------------------

    def emit(self, kind: str, **fields) -> dict:
        ev = {"ts": time.time(), "type": "ASSISTANT", "kind": kind, "raw": "", "interesting": False, **fields}
        ev.setdefault("text", "")
        try:
            self.emit_cb(ev)
        except Exception:
            pass
        return ev

    def _voice_active(self) -> bool:
        return bool(self.speaker.enabled and self.speaker.available)

    def _set_emotion(self, key: str) -> None:
        """Nuova emozione dal testo: faccia e suono subito; sul Mario fisico quando la frase viene
        pronunciata (se la voce è attiva), altrimenti subito."""
        changed = key != self.emotion
        self.emotion = key
        emo = EMOTIONS[key]
        self.emit("emotion", emotion=key, emoji=emo.emoji, color=emo.color, label=emo.label, text=emo.label)
        if not self._voice_active():
            self.behaviours.express(key)
        if changed and emo.sound:
            self.speaker.play(emo.sound)

    def _on_speaking(self, text: str, emotion: Optional[str]) -> None:
        """Chiamato dal thread della voce quando inizia una frase."""
        if emotion in EMOTIONS:
            self.behaviours.express(emotion)
        self.emit("speaking", text=text, emotion=emotion)

    # ----- stato -------------------------------------------------------------------------

    def status(self) -> dict:
        emo = EMOTIONS[self.emotion]
        return {"listening": self.listening, "busy": self.busy, "turn": self.turn, "pending": len(self.pending),
                "speaking": not self.speaker.idle,
                "emotion": {"key": self.emotion, "emoji": emo.emoji, "color": emo.color, "label": emo.label},
                "backend": self.backend_name, "last_answer": self.last_answer, "last_error": self.last_error,
                "history": len(self.history), "context": {**self.mapper.context, "text": self.mapper.context_text()},
                "intents": [i.to_dict() for i in list(self.intents)[-10:]],
                "speaker": self.speaker.status(), "behaviours": self.behaviours.status(),
                "rules": self.mapper.rules_dict(), "emotions": [EMOTIONS[k].to_dict() for k in EMOTIONS]}

    def build_system_prompt(self) -> str:
        return BASE_PROMPT.format(tags=" ".join(f"[{k}]" for k in MODEL_EMOTIONS),
                                  persona=self.persona.strip(), context=self.mapper.context_text()).strip()

    # ----- ingresso -----------------------------------------------------------------------

    def set_listening(self, on: bool, source: str = "dashboard") -> bool:
        self.listening = bool(on)
        self.emit("listening", on=self.listening, source=source,
                  text="ascolto dei sensori attivo" if self.listening else "ascolto dei sensori sospeso")
        self.speaker.play("listen_on" if self.listening else "listen_off")
        return self.listening

    def handle_event(self, ev: dict) -> list[Intent]:
        intents = self.mapper.feed(ev)
        if ev.get("type") == "PORT_VALUE_SINGLE" and ev.get("io_type") in (0x4A, 0x49) or intents:
            self.emit("context", context=self.mapper.context, text=self.mapper.context_text())
        self._dispatch(intents)
        return intents

    def tick(self) -> list[Intent]:
        """Da chiamare ogni mezzo secondo circa: promuove pantaloni e colore rimasti stabili."""
        intents = self.mapper.tick()
        if intents:
            self.emit("context", context=self.mapper.context, text=self.mapper.context_text())
            self._dispatch(intents)
        return intents

    def _dispatch(self, intents: list[Intent]) -> None:
        for intent in intents:
            self.intents.append(intent)
            outcome = self._apply(intent)
            self.emit("intent", intent=intent.to_dict(), outcome=outcome, text=f"{intent.source}: {outcome}")

    def _apply(self, intent: Intent) -> str:
        if intent.kind == "control":
            if intent.text == "toggle_listen":
                self.set_listening(not self.listening, source=intent.source)
                return "ascolto " + ("attivato" if self.listening else "sospeso")
            if intent.text == "repeat":
                return "ripeto l'ultima risposta" if self.repeat() else "niente da ripetere"
            if intent.text == "stop":
                self.stop()
                return "fermato"
            return f"controllo sconosciuto {intent.text}"
        if not self.listening:
            return "ignorato: ascolto sospeso"
        if (self.busy or not self.speaker.idle) and intent.priority < 2:
            self._enqueue(intent)
            return "in attesa: Mario sta ancora parlando"
        try:
            self.ask(intent.text, source=intent.source, priority=intent.priority, sensor=True)
        except AssistantBusy:
            self._enqueue(intent)
            return "in attesa: Mario sta ancora parlando"
        except Exception as exc:
            return f"errore: {exc}"
        return "inviato al modello"

    def _enqueue(self, intent: Intent) -> None:
        """Tiene l'ultimo intento per regola; verranno uniti in un solo prompt quando Mario tace."""
        self.pending = [i for i in self.pending if i.name != intent.name] + [intent]
        del self.pending[:-5]

    def _flush_pending(self) -> None:
        if not self.pending or self.busy or not self.listening or not self.speaker.idle:
            return
        now = time.time()
        fresh = [i for i in self.pending if now - i.ts <= self.pending_ttl]
        dropped = len(self.pending) - len(fresh)
        self.pending = []
        if dropped:
            self.emit("intent_dropped", count=dropped, text=f"{dropped} eventi vecchi scartati")
        if not fresh:
            return
        if len(fresh) == 1:
            text, source = fresh[0].text, fresh[0].source
        else:
            text = "Nel frattempo sono successe più cose: " + " ".join(i.text for i in fresh)
            source = "sensori: " + ", ".join(i.source.split(": ", 1)[-1] for i in fresh)
        try:
            self.ask(text, source=source, sensor=True)
            self.emit("intent", intent=fresh[-1].to_dict(), outcome="inviato al modello (dalla coda)",
                      text=f"{source}: inviato al modello (dalla coda)")
        except (AssistantBusy, ValueError):
            pass

    # ----- turni ----------------------------------------------------------------------------

    def ask(self, text: str, source: str = "utente", priority: int = 1, sensor: bool = False) -> int:
        text = text.strip()
        if not text:
            raise ValueError("testo vuoto")
        with self._lock:
            if self.busy:
                if priority < 2:
                    raise AssistantBusy("sto già rispondendo: aspetta la fine o premi Stop")
                self._abort = True
                self.speaker.stop()
            elif not self.speaker.idle and (not sensor or priority >= 2):
                self.speaker.stop()  # chi scrive (o una caduta) ha la precedenza sulla voce in corso
            self.busy = True
            self.turn += 1
            turn = self.turn
        prompt = f"[sensore: {source}] {text} (Rispondi con una frase breve, al massimo due.)" if sensor else text
        ctx = self.mapper.context_text()
        if ctx != self._ctx_sent:
            prompt = f"(Stato del corpo: {ctx})\n{prompt}"
            self._ctx_sent = ctx
        self._thread = threading.Thread(target=self._run_turn, args=(turn, text, source, prompt, sensor),
                                        name=f"assistant-turn-{turn}", daemon=True)
        self._thread.start()
        return turn

    def _run_turn(self, turn: int, text: str, source: str, prompt: str, sensor: bool) -> None:
        started = time.time()
        # se un turno precedente sta ancora finendo, gli lasciamo il tempo di uscire
        while self._abort and self._thread is not threading.current_thread():
            time.sleep(0.02)
        self._abort = False
        self.emit("user", text=text, source=source, sensor=sensor, turn=turn)
        self.on_message({"role": "user", "content": text, "emotion": None, "source": source, "turn": turn, "ts": started})
        self.behaviours.express("attesa")
        parser, sentences = EmotionStream(), SentenceBuffer()
        answer_raw: list[str] = []
        try:
            backend = self.backend_factory()
            self.backend_name = backend.describe()
            self.emit("start", turn=turn, backend=self.backend_name, text=f"chiedo a {self.backend_name}")
            messages = self.history + [{"role": "user", "content": prompt}]
            stream = backend.stream(self.build_system_prompt(), messages)
            try:
                for chunk in stream:
                    if self._abort:
                        break
                    answer_raw.append(chunk)
                    self._consume(parser.feed(chunk), sentences, turn)
                if not self._abort:
                    self._consume(parser.flush(), sentences, turn)
                    rest = sentences.flush()
                    if rest:
                        self._sentence(rest, turn)
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()
            full_raw = "".join(answer_raw)
            plain = strip_tags(full_raw)
            if self._abort:
                self.emit("stopped", turn=turn, text=plain)
            else:
                self.last_answer = plain
                self.last_error = ""
                self.history += [{"role": "user", "content": prompt}, {"role": "assistant", "content": full_raw}]
                del self.history[:-self.history_limit]
                self.on_message({"role": "assistant", "content": plain, "emotion": self.emotion, "source": self.backend_name,
                                 "turn": turn, "ts": time.time()})
                self.emit("done", turn=turn, text=plain, emotions=parser.seen, elapsed=round(time.time() - started, 2))
        except LLMError as exc:
            self.last_error = str(exc)
            self.emit("error", turn=turn, text=str(exc))
            self._set_emotion("tristezza")
            self.speaker.speak(FALLBACK_ERROR_SPEECH, "tristezza")
        except Exception as exc:  # pragma: no cover - rete di sicurezza
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.emit("error", turn=turn, text=self.last_error)
        finally:
            self.busy = False
            self._abort = False
            if not self._voice_active():
                self._flush_pending()

    def _on_spoken(self, text: str, emotion: Optional[str]) -> None:
        """Chiamato dal thread della voce alla fine di ogni frase: se la coda è vuota, tocca agli eventi in attesa."""
        if self.speaker.idle:
            self._flush_pending()

    def _consume(self, items: list[tuple[str, str]], sentences: SentenceBuffer, turn: int) -> None:
        for kind, value in items:
            if kind == "emotion":
                rest = sentences.flush()  # la frase precedente finisce dove inizia il nuovo tag
                if rest:
                    self._sentence(rest, turn)
                self._set_emotion(value)
            else:
                self.emit("delta", turn=turn, text=value, emotion=self.emotion)
                for sentence in sentences.feed(value):
                    self._sentence(sentence, turn)

    def _sentence(self, sentence: str, turn: int) -> None:
        self.emit("sentence", turn=turn, text=sentence, emotion=self.emotion)
        self.speaker.speak(sentence, self.emotion)

    # ----- comandi ----------------------------------------------------------------------------

    def stop(self) -> None:
        if self.busy:
            self._abort = True
        self.pending.clear()
        self.speaker.stop()
        self.behaviours.restore()
        self.emit("stopped", text="fermato")

    def shutdown(self) -> None:
        """Fine sessione: ferma tutto e riporta Mario al volume di base prima di scollegarsi."""
        if self.busy:
            self._abort = True
        self.pending.clear()
        self.speaker.stop()
        self.speaker.on_start = None
        self.speaker.on_end = None
        self.behaviours.shutdown()

    def reset(self) -> None:
        self.stop()
        self.history.clear()
        self.last_answer = ""
        self._ctx_sent = ""
        self.emotion = DEFAULT_EMOTION
        self.emit("reset", text="nuova conversazione")

    def repeat(self) -> bool:
        if not self.last_answer:
            return False
        self.speaker.stop()
        buf = SentenceBuffer()
        parts = buf.feed(self.last_answer)
        rest = buf.flush()
        for sentence in parts + ([rest] if rest else []):
            self.speaker.speak(sentence, self.emotion)
        self.emit("repeat", text=self.last_answer)
        return True

    def say(self, text: str, emotion: Optional[str] = None) -> None:
        """Pronuncia un testo senza interrogare il modello (prova della voce o uscita diretta)."""
        if emotion in EMOTIONS:
            self._set_emotion(emotion)
        buf = SentenceBuffer()
        parts = buf.feed(text)
        rest = buf.flush()
        for sentence in parts + ([rest] if rest else []):
            self.emit("sentence", text=sentence, emotion=self.emotion)
            self.speaker.speak(sentence, self.emotion)
