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

import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from .behaviours import BehaviourEngine
from .emotions import DEFAULT_EMOTION, EMOTIONS, MODEL_EMOTIONS, EmotionStream, SentenceBuffer, strip_tags
from .intents import Intent, IntentMapper, tile_family
from .llm import Backend, LLMError
from .memory import ListMemory, MemoryStore, memory_prompt
from .tts import Speaker

BASE_PROMPT = """Sei Mario, il personaggio LEGO Super Mario: una statuina vera, con dei sensori, che parla attraverso il computer di casa. Parli italiano, in prima persona, con frasi brevi, allegre e adatte a bambini dai sei anni. Niente elenchi, niente markdown, niente emoji: solo frasi. Al massimo tre frasi, salvo che ti chiedano una storia.
Ogni frase inizia con un'emozione tra parentesi quadre, scelta solo tra: {tags}. Esempio: "[gioia] Yahoo, che salto! [calma] Adesso riposiamoci un momento."
I messaggi che iniziano con "[sensore: ...]" non li scrive una persona: arrivano dal tuo corpo (gesti, pantaloni, basette con il codice a barre, colore del terreno, pulsante, batteria). Reagisci come farebbe Mario nel gioco, in una o due frasi.
Le righe "(Stato del corpo: ...)" descrivono come sei in questo momento: tienine conto senza ripeterle.
{memory_rule}
{wait_rule}
{sound_rule}
{persona}
{memory}
Stato del corpo adesso: {context}."""

MEMORY_RULE = ("Se scopri qualcosa che vale la pena ricordare per le prossime volte (il nome di chi gioca, un gusto, "
               "una cosa successa), aggiungi alla fine un tag [ricorda: una frase breve in terza persona]. "
               "Al massimo uno per risposta, e solo se è davvero nuovo.")

SOUND_RULE = ("Puoi far sentire la tua vera voce del videogioco con un'esclamazione: scrivi il suo nome tra parentesi "
              "graffe all'inizio della risposta o tra due frasi, al massimo una per risposta e non in tutte. "
              "Non la ripetere a parole. Esclamazioni: {items}.")
SOUNDS_PER_TURN = 2

WAIT_RULE = ("Nelle storie e nei giochi puoi chiedere a chi gioca di fare un'azione con te e aspettarla: scrivi alla "
             "fine della risposta [aspetta: azione], con azione tra: {targets}. Puoi precisare pantaloni, basetta o "
             "terreno, per esempio [aspetta: pantaloni Fuoco], [aspetta: basetta nemico], [aspetta: terreno blu]. "
             "Poi il tuo corpo aspetta fino a {seconds:g} secondi e ti scrive \"[sensore: sfida ...]\" con quello che è "
             "successo: allora continua la storia. Chiedi l'azione in modo naturale, al massimo una per risposta e non "
             "in tutte; non chiedere mai di far cadere o lanciare Mario.")
# parola del tag [aspetta: ...] -> regola dei sensori che la soddisfa
WAIT_TARGETS = {"salto": "salto", "salta": "salto", "saltare": "salto", "giro": "rotazione", "gira": "rotazione",
                "rotazione": "rotazione", "pantaloni": "pantaloni", "basetta": "basetta", "terreno": "terreno",
                "colore": "terreno", "scossa": "scossa", "scuoti": "scossa", "pulsante": "pulsante",
                "bottone": "pulsante"}
WAIT_TARGET_NAMES = {"salto": "salto", "rotazione": "giro", "pantaloni": "pantaloni", "basetta": "basetta",
                     "terreno": "terreno", "scossa": "scossa", "pulsante": "pulsante"}
TILE_FAMILY_WORDS = {"nemico": "enemy", "nemica": "enemy", "amico": "friend", "amica": "friend", "premio": "prize",
                     "partenza": "start", "start": "start", "traguardo": "goal", "arrivo": "goal"}
WAIT_SECONDS = 20.0
WAIT_TIMEOUTS_IN_ROW = 2   # attese scadute di fila: alla seconda Mario smette di chiedere (nessuno gioca più)


@dataclass
class Challenge:
    """Un'azione chiesta dal modello con [aspetta: ...] e attesa dai sensori."""
    rule: str                        # regola dei sensori che la soddisfa (salto, pantaloni, basetta, ...)
    value: str                       # precisazione in minuscolo ("fuoco", "nemico", "blu"); "" = qualsiasi
    text: str                        # come l'ha scritta il modello
    turn: int
    started: Optional[float] = None  # inizio dell'attesa: quando Mario ha finito di dire la richiesta

    def to_dict(self) -> dict:
        return asdict(self)


def parse_challenge(text: str, turn: int = 0, enabled: Optional[set] = None) -> Optional[Challenge]:
    """'pantaloni Fuoco' -> Challenge('pantaloni', 'fuoco', ...); None se l'azione non si può verificare."""
    words = text.strip().strip(".!").split()
    if not words:
        return None
    rule = WAIT_TARGETS.get(words[0].lower())
    if rule is None or enabled is not None and rule not in enabled:
        return None
    value = " ".join(words[1:]).lower().strip(" '\"")
    if rule not in ("pantaloni", "basetta", "terreno"):
        value = ""
    return Challenge(rule, value, text.strip(), turn)

FALLBACK_ERROR_SPEECH = "Ops, adesso non riesco a rispondere."
BLOCKED_SPEECH = "Mmm, di questo preferisco non parlare. Facciamo un bel salto, piuttosto!"
TIME_UP_SPEECH = "Che bella giocata! Adesso Mario deve riposare un po'. Alla prossima avventura!"


class AssistantBusy(Exception):
    pass


class AssistantSession:
    def __init__(self, backend_factory: Callable[[], Backend], speaker: Speaker, behaviours: BehaviourEngine,
                 emit: Optional[Callable[[dict], None]] = None, mapper: Optional[IntentMapper] = None,
                 persona: str = "", history_limit: int = 20,
                 on_message: Optional[Callable[[dict], None]] = None,
                 memory: Optional[MemoryStore] = None, memory_enabled: bool = True,
                 session_limit: float = 0.0, blocked_words: Optional[list[str]] = None,
                 exclamations: bool = True, sensor_backend_factory: Optional[Callable[[], Backend]] = None,
                 challenges: bool = True, wait_seconds: float = WAIT_SECONDS):
        self.backend_factory = backend_factory
        self.sensor_backend_factory = sensor_backend_factory  # turni "[sensore: ...]": modello più rapido (o None)
        self.speaker = speaker
        self.behaviours = behaviours
        self.emit_cb = emit or (lambda ev: None)
        self.mapper = mapper or IntentMapper()
        self.persona = persona
        self.history_limit = history_limit
        self.on_message = on_message or (lambda msg: None)
        self.memory: MemoryStore = memory if memory is not None else ListMemory()
        self.memory_enabled = memory_enabled
        self.session_limit = float(session_limit or 0.0)   # secondi di gioco per sessione (0 = nessun limite)
        self.blocked_words: list[str] = [w for w in (blocked_words or []) if w]
        self.exclamations_enabled = exclamations   # {letsago} nelle risposte = esclamazione originale di Mario
        self.challenges_enabled = challenges        # [aspetta: salto] nelle risposte = azione attesa dai sensori
        self.wait_seconds = float(wait_seconds or WAIT_SECONDS)
        self.challenge: Optional[Challenge] = None
        self._timeouts_in_row = 0
        self._turn_sounds = 0
        self.started = time.time()
        self.time_up = False
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
                "speaking": not self.speaker.idle, "elapsed": round(time.time() - self.started),
                "session_limit": self.session_limit, "time_up": self.time_up,
                "memory": self.memory.list() if self.memory_enabled else [], "memory_enabled": self.memory_enabled,
                "blocked_words": list(self.blocked_words),
                "emotion": {"key": self.emotion, "emoji": emo.emoji, "color": emo.color, "label": emo.label},
                "backend": self.backend_name, "last_answer": self.last_answer, "last_error": self.last_error,
                "history": len(self.history), "context": {**self.mapper.context, "text": self.mapper.context_text()},
                "intents": [i.to_dict() for i in list(self.intents)[-10:]],
                "challenge": self.challenge.to_dict() if self.challenge else None,
                "challenges_enabled": self.challenges_enabled, "wait_seconds": self.wait_seconds,
                "speaker": self.speaker.status(), "behaviours": self.behaviours.status(),
                "rules": self.mapper.rules_dict(), "emotions": [EMOTIONS[k].to_dict() for k in EMOTIONS]}

    def available_exclamations(self) -> dict[str, tuple[str, str]]:
        """Esclamazioni che il modello può usare: attive, con la voce accesa e un file da suonare."""
        pack = getattr(self.speaker, "pack", None)
        if not (self.exclamations_enabled and self._voice_active() and pack is not None):
            return {}
        return pack.exclamations()

    def wait_targets(self) -> list[str]:
        """Le azioni che il modello può chiedere con [aspetta: ...]: quelle con la regola dei sensori attiva."""
        if not self.challenges_enabled:
            return []
        rules = self.mapper.rules
        return [name for rule, name in WAIT_TARGET_NAMES.items() if rule in rules and rules[rule].enabled]

    def build_system_prompt(self) -> str:
        memories = memory_prompt(self.memory.list()) if self.memory_enabled else ""
        found = self.available_exclamations()
        sound_rule = SOUND_RULE.format(items="; ".join(f"{{{n}}} = \"{phrase}\", {when}" for n, (phrase, when)
                                                       in found.items())) if found else ""
        targets = self.wait_targets()
        wait_rule = WAIT_RULE.format(targets=", ".join(targets), seconds=self.wait_seconds) if targets else ""
        text = BASE_PROMPT.format(tags=" ".join(f"[{k}]" for k in MODEL_EMOTIONS), persona=self.persona.strip(),
                                  memory_rule=MEMORY_RULE if self.memory_enabled else "", memory=memories,
                                  wait_rule=wait_rule, sound_rule=sound_rule, context=self.mapper.context_text())
        return "\n".join(line for line in text.splitlines() if line.strip()).strip()

    # ----- sicurezza: parole bloccate e limite di tempo --------------------------------------------

    def configure_safety(self, session_limit: Optional[float] = None, blocked_words: Optional[list[str]] = None,
                         memory_enabled: Optional[bool] = None) -> None:
        if session_limit is not None:
            self.session_limit = max(0.0, float(session_limit))
            if self.time_up and (not self.session_limit or time.time() - self.started < self.session_limit):
                self.time_up = False
        if blocked_words is not None:
            self.blocked_words = [w.strip() for w in blocked_words if w and w.strip()]
        if memory_enabled is not None:
            self.memory_enabled = bool(memory_enabled)

    def _blocked_pattern(self) -> Optional[re.Pattern]:
        if not self.blocked_words:
            return None
        return re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in self.blocked_words) + r")(?!\w)", re.IGNORECASE)

    def _blocked_in(self, text: str) -> Optional[str]:
        pat = self._blocked_pattern()
        m = pat.search(text) if pat else None
        return m.group(1) if m else None

    def _clean(self, text: str) -> str:
        pat = self._blocked_pattern()
        return pat.sub("…", text) if pat else text

    def _check_time(self) -> bool:
        """True se la sessione può continuare; altrimenti (una volta sola) saluta e sospende l'ascolto."""
        if not self.session_limit or time.time() - self.started < self.session_limit:
            return True
        if not self.time_up:
            self.time_up = True
            self.emit("time_up", text=f"tempo di gioco finito ({self.session_limit / 60:g} min): ascolto sospeso")
            self._set_emotion("sonno")
            self.speaker.speak(TIME_UP_SPEECH, "sonno")
            self.listening = False
            self.challenge = None
        return False

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
        self._check_challenge()
        return intents

    def tick(self) -> list[Intent]:
        """Da chiamare ogni mezzo secondo circa: promuove pantaloni e colore rimasti stabili e controlla
        il tempo dell'azione attesa."""
        intents = self.mapper.tick()
        if intents:
            self.emit("context", context=self.mapper.context, text=self.mapper.context_text())
            self._dispatch(intents)
        self._check_challenge()
        return intents

    def _dispatch(self, intents: list[Intent]) -> None:
        for intent in intents:
            self.intents.append(intent)
            outcome = self._challenge_outcome(intent) or self._apply(intent)
            self.emit("intent", intent=intent.to_dict(), outcome=outcome, text=f"{intent.source}: {outcome}")

    # ----- storia interattiva: [aspetta: ...] ------------------------------------------------------

    def _start_challenge(self, text: str, turn: int) -> None:
        enabled = {rule for rule in WAIT_TARGET_NAMES if rule in self.mapper.rules and self.mapper.rules[rule].enabled}
        challenge = parse_challenge(text, turn, enabled)
        if challenge is None:
            self.emit("challenge_invalid", turn=turn, text=f"azione da aspettare non riconosciuta: {text}")
            return
        if not self._voice_active() or self.speaker.idle:
            challenge.started = time.time()
        self.challenge = challenge
        self.emit("challenge", turn=turn, challenge=challenge.to_dict(), seconds=self.wait_seconds,
                  text=f"aspetto: {challenge.text} (fino a {self.wait_seconds:g} s)")

    def _challenge_matches(self, challenge: Challenge, intent: Intent) -> bool:
        if not challenge.value:
            return True
        got = intent.source.split(": ", 1)[-1].lower()
        if challenge.value in got or got and got in challenge.value:
            return True
        family = TILE_FAMILY_WORDS.get(challenge.value)
        tile = self.mapper.context.get("tile")
        return challenge.rule == "basetta" and family is not None and tile is not None and tile_family(tile) == family

    def _challenge_outcome(self, intent: Intent) -> Optional[str]:
        """Se c'è un'azione attesa: la giusta (o una dello stesso tipo ma sbagliata) diventa un messaggio
        "[sensore: sfida ...]" per il modello; gli altri sensori vengono ignorati per non perdere il filo del
        gioco, tranne cadute e comandi (pulsante, scossa) che funzionano come sempre."""
        challenge = self.challenge
        if challenge is None or not self.listening:
            return None
        if intent.name != challenge.rule:
            if intent.kind == "prompt" and intent.priority < 2:
                return f"ignorato: Mario aspetta {challenge.text}"
            return None
        self.challenge = None
        self._timeouts_in_row = 0
        detail = f" {intent.text}" if intent.kind == "prompt" else ""
        if self._challenge_matches(challenge, intent):
            self.emit("challenge_done", challenge=challenge.to_dict(), source=intent.source,
                      text=f"sfida riuscita: {challenge.text} ({intent.source})")
            text, source = f"Ce l'ha fatta, ha fatto quello che avevi chiesto ({challenge.text})!{detail}", "sfida riuscita"
        else:
            self.emit("challenge_missed", challenge=challenge.to_dict(), source=intent.source,
                      text=f"sfida: chiesto {challenge.text}, arrivato {intent.source}")
            text, source = f"Avevi chiesto {challenge.text}, invece è arrivato: {intent.source}.{detail}", "sfida sbagliata"
        return self._apply(Intent("prompt", "sfida", text, f"{source}: {intent.source}", 1, intent.ts))

    def _check_challenge(self, now: Optional[float] = None) -> None:
        challenge, now = self.challenge, now or time.time()
        if challenge is None or challenge.started is None or self.busy or not self.speaker.idle:
            return
        if now - challenge.started < self.wait_seconds:
            return
        self.challenge = None
        self._timeouts_in_row += 1
        if self._timeouts_in_row >= WAIT_TIMEOUTS_IN_ROW:
            self.emit("challenge_timeout", challenge=challenge.to_dict(), final=True,
                      text=f"tempo scaduto per {challenge.text} di nuovo: Mario smette di aspettare")
            return
        self.emit("challenge_timeout", challenge=challenge.to_dict(), final=False,
                  text=f"tempo scaduto: nessuno ha fatto {challenge.text}")
        text = (f"Sono passati {self.wait_seconds:g} secondi e nessuno ha fatto quello che avevi chiesto "
                f"({challenge.text}). Incoraggia, oppure cambia gioco; se chiedi un'azione, scrivi di nuovo "
                "[aspetta: ...].")
        self._apply(Intent("prompt", "sfida", text, "sfida: tempo scaduto", 1, now))

    def _apply(self, intent: Intent) -> str:
        if intent.kind == "control":
            if intent.text == "toggle_listen":
                self.set_listening(not self.listening, source=intent.source)
                return "ascolto " + ("attivato" if self.listening else "sospeso")
            if intent.text == "repeat":
                if self.busy or not self.speaker.idle:  # scossa mentre parla: è un "basta", non un "ripeti"
                    self.stop()
                    return "fermato (scossa durante la risposta)"
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
            self.ask(intent.text, source=intent.source, priority=intent.priority, sensor=True,
                     quick=intent.name != "sfida")
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
            self.ask(text, source=source, sensor=True, quick=all(i.name != "sfida" for i in fresh))
            self.emit("intent", intent=fresh[-1].to_dict(), outcome="inviato al modello (dalla coda)",
                      text=f"{source}: inviato al modello (dalla coda)")
        except (AssistantBusy, ValueError):
            pass

    # ----- turni ----------------------------------------------------------------------------

    def ask(self, text: str, source: str = "utente", priority: int = 1, sensor: bool = False,
            quick: Optional[bool] = None) -> int:
        """Nuovo turno. `sensor`: il messaggio arriva dal corpo ("[sensore: ...]"); `quick` (di solito uguale a
        `sensor`): risposta breve con il modello dei sensori. Le sfide della storia interattiva sono messaggi dei
        sensori ma non rapidi: la storia la continua il modello della conversazione."""
        quick = sensor if quick is None else quick
        text = text.strip()
        if not text:
            raise ValueError("testo vuoto")
        if not self._check_time():
            raise AssistantBusy("tempo di gioco finito: premi Nuova (o /nuova) per ricominciare")
        if not sensor:
            self.challenge = None  # chi scrive (o parla) cambia discorso: niente più azione attesa
            self._timeouts_in_row = 0
            word = self._blocked_in(text)
            if word:
                self.emit("blocked", text=f"messaggio non inviato al modello (parola bloccata)", source=source)
                self._set_emotion("pensieroso")
                self.speaker.speak(BLOCKED_SPEECH, "pensieroso")
                return 0
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
        if sensor:
            hint = "Rispondi con una frase breve, al massimo due." if quick else "Continua la storia in due o tre frasi."
            prompt = f"[sensore: {source}] {text} ({hint})"
        else:
            prompt = text
        ctx = self.mapper.context_text()
        if ctx != self._ctx_sent:
            prompt = f"(Stato del corpo: {ctx})\n{prompt}"
            self._ctx_sent = ctx
        self._thread = threading.Thread(target=self._run_turn, args=(turn, text, source, prompt, sensor, quick),
                                        name=f"assistant-turn-{turn}", daemon=True)
        self._thread.start()
        return turn

    def _run_turn(self, turn: int, text: str, source: str, prompt: str, sensor: bool, quick: bool = False) -> None:
        started = time.time()
        self._first_sentence_at: Optional[float] = None
        # se un turno precedente sta ancora finendo, gli lasciamo il tempo di uscire
        while self._abort and self._thread is not threading.current_thread():
            time.sleep(0.02)
        self._abort = False
        self.emit("user", text=text, source=source, sensor=sensor, turn=turn)
        self.on_message({"role": "user", "content": text, "emotion": None, "source": source, "turn": turn, "ts": started})
        self.behaviours.express("attesa")
        parser, sentences = EmotionStream(), SentenceBuffer()
        answer_raw: list[str] = []
        self._turn_sounds = 0
        self._turn_allowed = self.available_exclamations()
        try:
            factory = self.sensor_backend_factory if quick and self.sensor_backend_factory else self.backend_factory
            backend = factory()
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
            plain = self._clean(strip_tags(full_raw))
            if self._abort:
                self.emit("stopped", turn=turn, text=plain)
            else:
                self.last_answer = plain
                self.last_error = ""
                self.history += [{"role": "user", "content": prompt}, {"role": "assistant", "content": full_raw}]
                del self.history[:-self.history_limit]
                self.on_message({"role": "assistant", "content": plain, "emotion": self.emotion, "source": self.backend_name,
                                 "turn": turn, "ts": time.time()})
                if self.challenges_enabled and parser.waits:
                    self._start_challenge(parser.waits[-1], turn)
                if self.memory_enabled:
                    for memory in parser.memories:
                        try:
                            mid = self.memory.add(memory)
                        except Exception as exc:  # il database non deve fermare la risposta
                            mid = 0
                            self.emit("error", turn=turn, text=f"memoria non salvata: {exc}")
                        if mid:
                            self.emit("memory", turn=turn, text=memory, id=mid)
                first = self._first_sentence_at
                self.emit("done", turn=turn, text=plain, emotions=parser.seen, elapsed=round(time.time() - started, 2),
                          first_sentence=round(first - started, 2) if first else None,
                          display=self._display(full_raw), sounds=parser.sounds)
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
        """Chiamato dal thread della voce alla fine di ogni frase: se la coda è vuota, tocca agli eventi in attesa
        e parte l'attesa dell'azione chiesta (Mario ha finito di chiederla)."""
        if self.speaker.idle:
            if self.challenge is not None and self.challenge.started is None and not self.busy:
                self.challenge.started = time.time()
            self._flush_pending()

    def _consume(self, items: list[tuple[str, str]], sentences: SentenceBuffer, turn: int) -> None:
        for kind, value in items:
            if kind == "emotion":
                rest = sentences.flush()  # la frase precedente finisce dove inizia il nuovo tag
                if rest:
                    self._sentence(rest, turn)
                self._set_emotion(value)
            elif kind in ("memory", "wait"):
                continue  # gestiti a fine risposta (vedi _run_turn), mai letti a voce
            elif kind == "sound":
                self._exclamation(value, sentences, turn)
            else:
                self.emit("delta", turn=turn, text=self._clean(value), emotion=self.emotion)
                for sentence in sentences.feed(value):
                    self._sentence(sentence, turn)

    def _exclamation(self, name: str, sentences: SentenceBuffer, turn: int) -> None:
        """{nome} nella risposta: l'esclamazione originale suona al suo posto tra le frasi (mai letta). Nomi
        sconosciuti o oltre il massimo per risposta vengono ignorati in silenzio."""
        allowed = getattr(self, "_turn_allowed", None)
        if allowed is None:
            allowed = self.available_exclamations()
        if name not in allowed or self._turn_sounds >= SOUNDS_PER_TURN:
            return
        self._turn_sounds += 1
        rest = sentences.flush()  # la frase in corso viene detta prima dell'esclamazione
        if rest:
            self._sentence(rest, turn)
        self.emit("sound", turn=turn, name=name, text=f"🔊 {allowed[name][0]}")
        self.speaker.speak_sound(name, "voce")

    def _display(self, raw: str) -> str:
        """Testo per la chat: senza tag, con le esclamazioni suonate scritte come 🔊 frase."""
        allowed = getattr(self, "_turn_allowed", None) or {}
        marked = re.sub(r"\{\s*([a-z0-9_]{1,24})\s*\}", lambda m: f"🔊 {allowed[m.group(1)][0]} " if m.group(1) in allowed
                        else m.group(0), raw)
        return self._clean(strip_tags(marked))

    def _sentence(self, sentence: str, turn: int) -> None:
        sentence = self._clean(sentence)
        if getattr(self, "_first_sentence_at", None) is None:
            self._first_sentence_at = time.time()
        self.emit("sentence", turn=turn, text=sentence, emotion=self.emotion)
        self.speaker.speak(sentence, self.emotion)

    # ----- comandi ----------------------------------------------------------------------------

    def stop(self) -> None:
        if self.busy:
            self._abort = True
        self.pending.clear()
        self.challenge = None
        self.speaker.stop()
        self.behaviours.restore()
        self.emit("stopped", text="fermato")

    def shutdown(self) -> None:
        """Fine sessione: ferma tutto e riporta Mario al volume di base prima di scollegarsi."""
        if self.busy:
            self._abort = True
        self.pending.clear()
        self.challenge = None
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
        self.started = time.time()
        if self.time_up:
            self.time_up = False
            self.listening = True
        self.emit("reset", text="nuova conversazione")

    def forget(self, memory_id: Optional[int] = None) -> int:
        """Cancella un ricordo (o tutti) e lo comunica."""
        n = self.memory.delete(memory_id) if memory_id else self.memory.clear()
        self.emit("memory_forgotten", text=("un ricordo cancellato" if memory_id else f"{n} ricordi cancellati"), id=memory_id, count=n)
        return n

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
