"""Evoluzioni dell'assistente (16/10): modello per i sensori, storia interattiva con [aspetta: ...], sicurezza
(temi, limite di messaggi, riepilogo per i genitori), ricordi (riassunto, scadenza, profili), ingresso vocale.
Senza rete né audio: backend a copione e voce finta."""
import importlib.util
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

from mario.assistant import AssistantSession, LLMConfig, Speaker
from mario.assistant.behaviours import BehaviourEngine, NullActions

HAVE_WEB = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("bleak") is not None


def wait_idle(session, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not session.busy:
            return True
        time.sleep(0.01)
    return False


class ScriptedBackend:
    def __init__(self, text, name="copione"):
        self.text = text
        self.model = name
        self.calls = []

    def describe(self):
        return self.model

    def stream(self, system, messages):
        self.calls.append(messages[-1]["content"])
        self.system = system
        for word in self.text.split(" "):
            yield word + " "


def quiet_speaker():
    return Speaker(enabled=False, engine=None, sounds=False)


class SensorModelTests(unittest.TestCase):
    def test_resolved_sensor_model(self):
        env = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            claude = LLMConfig(backend="anthropic", model="claude-sonnet-5-5")
            self.assertEqual(claude.resolved_sensor_model(), "claude-haiku-5-5")
            self.assertEqual(claude.for_sensors().model, "claude-haiku-5-5")
            self.assertEqual(claude.for_sensors().max_tokens, claude.max_tokens)
            self.assertEqual(LLMConfig(api_key="sk-ant-xxxxxxxxxxxx").resolved_sensor_model(), "claude-haiku-5-5")
            self.assertEqual(LLMConfig(backend="demo").resolved_sensor_model(), "")  # demo: stesso backend
            self.assertEqual(LLMConfig(backend="openai", model="llama3.2").for_sensors().model, "llama3.2")
            same = LLMConfig(backend="anthropic", model="claude-sonnet-5-5", sensor_model="")
            self.assertEqual(same.for_sensors().model, "claude-sonnet-5-5")
            haiku = LLMConfig(backend="anthropic", model="claude-haiku-5-5")
            self.assertEqual(haiku.resolved_sensor_model(), "")  # già haiku: nessuna differenza
            self.assertEqual(LLMConfig(backend="openai", model="big", sensor_model="small").for_sensors().model, "small")
            self.assertIn("resolved_sensor_model", claude.to_dict())
        finally:
            if env is not None:
                os.environ["ANTHROPIC_API_KEY"] = env

    def test_session_uses_sensor_backend_only_for_sensors(self):
        chat, sensors, events = ScriptedBackend("[gioia] Ciao!", "grande"), ScriptedBackend("[gioia] Hop!", "rapido"), []
        session = AssistantSession(lambda: chat, quiet_speaker(), BehaviourEngine(NullActions()), emit=events.append,
                                   sensor_backend_factory=lambda: sensors)
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        session.ask("Ho appena fatto un salto!", source="gesto: salto", sensor=True)
        self.assertTrue(wait_idle(session))
        self.assertEqual((len(chat.calls), len(sensors.calls)), (1, 1))
        self.assertTrue(sensors.calls[0].startswith("[sensore: gesto: salto]"))
        self.assertIn("Ciao!", str(session.history))  # la cronologia è la stessa per i due modelli
        self.assertEqual([e["backend"] for e in events if e["kind"] == "start"], ["grande", "rapido"])
        alone = AssistantSession(lambda: chat, quiet_speaker(), BehaviourEngine(NullActions()))
        alone.ask("salto", sensor=True)  # senza factory dei sensori: il modello unico
        self.assertTrue(wait_idle(alone))
        self.assertEqual(len(chat.calls), 2)

    @unittest.skipUnless(HAVE_WEB, "servono flask e bleak (venv dell'host)")
    def test_service_setting(self):
        from mario.web import create_app
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(db_path=Path(tmp) / "t.sqlite3", start_worker=False, quiet_assistant=True)
            client = app.test_client()
            s = client.get("/api/assistant/settings").get_json()["settings"]
            self.assertEqual(s["sensor_model"], "auto")
            client.post("/api/assistant/settings", json={"backend": "anthropic", "model": "claude-sonnet-5-5",
                                                         "sensor_model": "claude-haiku-5-5"})
            service = app.extensions["assistant"]
            self.assertEqual(service._make_sensor_backend().model, "claude-haiku-5-5")
            self.assertEqual(service._make_backend().model, "claude-sonnet-5-5")
            st = client.get("/api/assistant/status").get_json()["status"]
            self.assertEqual(st["llm"]["resolved_sensor_model"], "claude-haiku-5-5")



class SequenceBackend:
    """Risponde con i testi in ordine (l'ultimo si ripete)."""
    model = "copione"

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def describe(self):
        return "copione"

    def stream(self, system, messages):
        self.calls.append(messages[-1]["content"])
        self.system = system
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        for word in text.split(" "):
            yield word + " "


def port_event(io_type, mode, data, **extra):
    return {"ts": time.time(), "type": "PORT_VALUE_SINGLE", "io_type": io_type, "mode": mode, "data": bytes(data),
            **extra}


JUMP = (0x47, 1, b"\x00\x80")
ROTATION = (0x47, 1, b"\x00\x01")
FALL = (0x47, 1, b"\x00\x08")


def tile(code):
    return (0x49, 0, code.to_bytes(2, "little") + b"\xff\xff")


class ChallengeTests(unittest.TestCase):
    def make(self, *texts, **kw):
        self.events = []
        self.backend = SequenceBackend(*texts)
        session = AssistantSession(lambda: self.backend, quiet_speaker(), BehaviourEngine(NullActions()),
                                   emit=self.events.append, **kw)
        return session

    def kinds(self, prefix="challenge"):
        return [e["kind"] for e in self.events if e["kind"].startswith(prefix)]

    def turn(self, session, text="ciao", **kw):
        session.ask(text, **kw)
        self.assertTrue(wait_idle(session))

    def test_parse_and_prompt(self):
        from mario.assistant.emotions import strip_tags, wait_tags
        from mario.assistant.session import parse_challenge
        self.assertEqual(parse_challenge("Salta!").rule, "salto")
        ch = parse_challenge("basetta Goomba", 3)
        self.assertEqual((ch.rule, ch.value, ch.turn), ("basetta", "goomba", 3))
        self.assertEqual(parse_challenge("giro su te stesso").value, "")  # solo pantaloni, basetta e terreno
        self.assertIsNone(parse_challenge("vola"))
        self.assertIsNone(parse_challenge("salto", enabled={"basetta"}))
        self.assertEqual(wait_tags("[gioia] Salta! [aspetta: salto]"), ["salto"])
        self.assertEqual(strip_tags("[gioia] Salta! [aspetta: salto] "), "Salta!")
        session = self.make("[gioia] Ciao!")
        prompt = session.build_system_prompt()
        self.assertIn("[aspetta: azione]", prompt)
        self.assertIn("salto, giro, pantaloni, basetta, terreno, scossa, pulsante", prompt)
        session.mapper.set_rule("rotazione", enabled=False)
        self.assertNotIn("giro", session.wait_targets())  # regola spenta: azione non verificabile
        session.challenges_enabled = False
        self.assertNotIn("[aspetta:", session.build_system_prompt())

    def test_jump_completes_and_story_continues(self):
        session = self.make("[gioia] Fai un salto con me! [aspetta: salto]", "[gioia] Bravissimo, che salto!")
        self.turn(session)
        self.assertEqual(session.challenge.rule, "salto")
        self.assertIsNotNone(session.challenge.started)  # niente voce: l'attesa parte subito
        self.assertEqual(session.status()["challenge"]["text"], "salto")
        self.assertNotIn("aspetta", session.last_answer)  # il tag non si legge
        session.handle_event(port_event(*ROTATION))  # sensore estraneo: ignorato
        outcomes = [e["outcome"] for e in self.events if e["kind"] == "intent"]
        self.assertEqual(outcomes, ["ignorato: Mario aspetta salto"])
        session.handle_event(port_event(*JUMP))
        self.assertTrue(wait_idle(session))
        self.assertIsNone(session.challenge)
        self.assertEqual(self.kinds(), ["challenge", "challenge_done"])
        self.assertIn("[sensore: sfida riuscita: gesto: salto] Ce l'ha fatta", self.backend.calls[-1])
        self.assertIn("Bravissimo", session.last_answer)

    def test_story_turns_use_conversation_model(self):
        quick = SequenceBackend("[gioia] Hop!")
        session = self.make("[gioia] Salta! [aspetta: salto]", "[gioia] Il cancello si apre!",
                            sensor_backend_factory=lambda: quick)
        self.turn(session)
        session.handle_event(port_event(*JUMP))  # sfida: la storia la continua il modello grande
        self.assertTrue(wait_idle(session))
        self.assertIn("(Continua la storia in due o tre frasi.)", self.backend.calls[-1])
        self.assertEqual(quick.calls, [])
        session.handle_event(port_event(*ROTATION))  # nessuna attesa: reazione rapida del modello dei sensori
        self.assertTrue(wait_idle(session))
        self.assertIn("(Rispondi con una frase breve, al massimo due.)", quick.calls[-1])

    def test_fall_passes_and_wrong_tile_then_family(self):
        session = self.make("[gioia] Trova una basetta nemico! [aspetta: basetta nemico]", "[sorpresa] Ops!",
                            "[gioia] Riprova! [aspetta: basetta nemico]", "[gioia] Evviva!")
        session.mapper.set_rule("basetta", cooldown=0)  # due basette di fila nel giro di pochi secondi
        self.turn(session)
        session.handle_event(port_event(*FALL))  # caduta: priorità alta, passa e non chiude l'attesa
        self.assertTrue(wait_idle(session))
        self.assertIsNotNone(session.challenge)
        session.handle_event(port_event(*tile(0x00B8)))  # Start Mario: non è un nemico
        self.assertTrue(wait_idle(session))
        self.assertEqual(self.kinds(), ["challenge", "challenge_missed", "challenge"])
        self.assertIn("Avevi chiesto basetta nemico, invece è arrivato: basetta:", self.backend.calls[-1])
        session.handle_event(port_event(*tile(0x0002)))  # Goomba: famiglia nemico
        self.assertTrue(wait_idle(session))
        self.assertEqual(self.kinds()[-1], "challenge_done")
        self.assertIn("Evviva", session.last_answer)

    def test_timeouts_and_user_text(self):
        session = self.make("[gioia] Salta! [aspetta: salto]", "[gioia] Dai, salta! [aspetta: salto]",
                            "[gioia] Ciao! [aspetta: salto]", "[calma] Va bene, parliamo.", wait_seconds=0.05)
        self.turn(session)
        time.sleep(0.1)
        session.tick()  # scaduta: il modello incoraggia e chiede di nuovo
        self.assertTrue(wait_idle(session))
        self.assertIn("nessuno ha fatto quello che avevi chiesto (salto)", self.backend.calls[-1])
        self.assertIsNotNone(session.challenge)
        time.sleep(0.1)
        session.tick()  # seconda di fila: Mario smette di aspettare, nessun nuovo turno
        self.assertTrue(wait_idle(session))
        timeouts = [e for e in self.events if e["kind"] == "challenge_timeout"]
        self.assertEqual([e["final"] for e in timeouts], [False, True])
        self.assertEqual(len(self.backend.calls), 2)
        self.assertIsNone(session.challenge)
        self.turn(session, "ciao di nuovo")  # chi scrive azzera il conteggio
        self.assertIsNotNone(session.challenge)
        session.ask("parliamo d'altro")  # e cambia discorso: niente più attesa
        self.assertTrue(wait_idle(session))
        self.assertIsNone(session.challenge)

    def test_invalid_and_disabled(self):
        session = self.make("[gioia] Vola! [aspetta: vola in cielo]")
        self.turn(session)
        self.assertIsNone(session.challenge)
        self.assertEqual(self.kinds(), ["challenge_invalid"])
        off = self.make("[gioia] Salta! [aspetta: salto]", challenges=False)
        self.turn(off)
        self.assertIsNone(off.challenge)
        self.assertEqual(off.last_answer, "Salta!")

    def test_waits_for_speech_before_timer(self):
        class SilentSpeaker(Speaker):
            def _run(self):  # le frasi restano in coda: Mario "sta parlando"
                pass
        speaker = SilentSpeaker(enabled=True, engine="spd-say")
        backend = SequenceBackend("[gioia] Salta! [aspetta: salto]")
        session = AssistantSession(lambda: backend, speaker, BehaviourEngine(NullActions()), wait_seconds=0.01)
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        self.assertIsNone(session.challenge.started)  # la richiesta non è ancora stata detta
        time.sleep(0.05)
        session.tick()
        self.assertIsNotNone(session.challenge)  # niente scadenza mentre parla
        speaker.stop()
        session._on_spoken("Salta!", "gioia")
        self.assertIsNotNone(session.challenge.started)


FAKE_STT = """
import json, sys, wave
print(json.dumps({"event": "ready", "load_s": 0.01, "model": "finto", "threads": 1}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    if req.get("cmd") == "quit":
        break
    if req.get("cmd") == "transcribe":
        with wave.open(req["path"]) as w:
            secs = w.getnframes() / w.getframerate()
        text = "Sottotitoli creati dalla comunità Amara.org" if secs > 1.4 else "Facciamo un salto insieme"
        print(json.dumps({"id": req["id"], "ok": True, "text": text, "audio_s": round(secs, 2),
                          "speech_s": round(secs, 2), "elapsed_s": 0.01}), flush=True)
"""


def write_wav(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x01" * int(16000 * seconds))


class VoiceInputTests(unittest.TestCase):
    def setUp(self):
        from mario.assistant import stt
        self.stt = stt
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "worker.py").write_text(FAKE_STT, encoding="utf-8")
        self._tmp_dir = stt.TMP_DIR
        stt.TMP_DIR = self.dir / "registrazioni"

    def tearDown(self):
        self.stt.TMP_DIR = self._tmp_dir
        self.tmp.cleanup()

    def voice(self, seconds):
        src = self.dir / f"sorgente-{seconds}.wav"
        write_wav(src, seconds)
        client = self.stt.SpeechClient(command=[sys.executable, str(self.dir / "worker.py")])
        recorder = self.stt.Recorder(name="finto", command=["sh", "-c", f"cp {src} {{path}}; exec sleep 30"])
        return self.stt.VoiceInput(client=client, recorder=recorder)

    def session_with(self, vi, *texts):
        self.events = []
        self.backend = SequenceBackend(*texts or ("[gioia] Andiamo!",))
        session = AssistantSession(lambda: self.backend, quiet_speaker(), BehaviourEngine(NullActions()),
                                   emit=self.events.append, voice_input=vi)
        return session

    def wait_kind(self, kinds, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = [e for e in self.events if e["kind"] in kinds]
            if found:
                return found[-1]
            time.sleep(0.02)
        self.fail(f"nessun evento {kinds}: {[e['kind'] for e in self.events]}")

    def button(self, session, pressed):
        return session.handle_event({"ts": time.time(), "type": "HUB_PROPERTIES", "prop": "BUTTON", "value": pressed})

    def test_helpers(self):
        stt = self.stt
        self.assertEqual(stt.clean_transcript("  Ciao   Mario! "), "Ciao Mario!")
        self.assertEqual(stt.clean_transcript("Sottotitoli a cura di QTSS"), "")
        self.assertEqual(stt.clean_transcript(" ... "), "")
        self.assertEqual(stt.clean_transcript("Grazie per la visione!"), "")
        path = self.dir / "a.wav"
        write_wav(path, 0.5)
        self.assertAlmostEqual(stt.wav_seconds(path), 0.5, places=2)
        raw = self.dir / "troncato.wav"
        raw.write_bytes(b"RIFF" + bytes(40) + b"\x00" * 32000)  # intestazione non chiusa: stima dai byte
        self.assertAlmostEqual(stt.wav_seconds(raw), 1.0, places=2)
        self.assertIn("setup_stt.sh", stt.problem(python=self.dir / "manca"))
        argv = stt.SpeechClient("base")._argv()
        self.assertEqual(argv[1:3], ["-I", str(stt.WORKER)])
        self.assertEqual(argv[argv.index("--model") + 1], str(stt.MODELS_DIR / "base"))

    def test_mario_button_is_push_to_talk(self):
        vi = self.voice(1.0)
        session = self.session_with(vi)
        self.assertIn("(a voce)", session.build_system_prompt())
        self.assertNotIn("pulsante", session.wait_targets())  # il pulsante serve per parlare
        self.assertEqual(self.button(session, True), [])
        self.assertTrue(vi.listening)
        self.assertEqual(self.wait_kind({"voice_listening"})["source"], "pulsante di Mario")
        time.sleep(0.2)
        self.button(session, False)
        ev = self.wait_kind({"voice_text"})
        self.assertEqual(ev["text"], "Facciamo un salto insieme")
        self.assertTrue(wait_idle(session))
        self.assertIn("(a voce) Facciamo un salto insieme", self.backend.calls[-1])
        self.assertTrue(session.listening)  # il pulsante non ha cambiato l'ascolto dei sensori
        self.assertEqual(list((self.dir / "registrazioni").glob("*.wav")), [])  # l'audio non resta
        self.assertEqual(session.status()["voice"]["worker"]["transcriptions"], 1)
        session.shutdown()
        self.assertEqual(vi.client.state, "spento")

    def test_short_press_and_silence(self):
        short = self.voice(0.1)
        session = self.session_with(short)
        session.listen_start()
        time.sleep(0.1)
        session.listen_stop()
        self.assertIn("troppo poco", self.wait_kind({"voice_empty"})["text"])
        self.assertEqual(self.backend.calls, [])
        session.shutdown()
        noise = self.voice(1.5)  # Whisper "sente" i sottotitoli nel silenzio: scartati
        session = self.session_with(noise)
        session.listen_start("tastiera")
        time.sleep(0.2)
        session.listen_stop()
        self.assertIn("non ho capito", self.wait_kind({"voice_empty"})["text"])
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(session.emotion, "pensieroso")
        session.shutdown()

    def test_without_voice_input(self):
        events = []
        session = AssistantSession(lambda: SequenceBackend("[gioia] Ciao"), quiet_speaker(),
                                   BehaviourEngine(NullActions()), emit=events.append)
        self.assertFalse(session.listen_start())
        self.assertEqual(events[-1]["kind"], "voice_error")
        self.assertNotIn("(a voce)", session.build_system_prompt())
        self.button(session, True)  # senza ingresso vocale il pulsante attiva o sospende l'ascolto, come prima
        self.assertFalse(session.listening)

    @unittest.skipUnless(HAVE_WEB, "servono flask e bleak (venv dell'host)")
    def test_settings_and_api(self):
        from mario.web import create_app
        app = create_app(db_path=self.dir / "t.sqlite3", start_worker=False, quiet_assistant=True)
        client = app.test_client()
        s = client.post("/api/assistant/settings", json={"voice_input": 1, "stt_model": "base"}).get_json()["settings"]
        self.assertEqual((s["voice_input"], s["voice_button"], s["stt_model"]), (True, True, "base"))
        self.assertIsNone(app.extensions["assistant"].session.voice_input)  # silenzioso (test): niente microfono
        self.assertEqual(client.post("/api/assistant/talk", json={"on": 1}).status_code, 409)
        self.assertTrue(client.post("/api/assistant/talk", json={"on": 0}).get_json()["ok"])


class SafetyTests(unittest.TestCase):
    def test_theme_filter(self):
        from mario.assistant.safety import THEMES, ThemeFilter, parse_themes
        f = ThemeFilter()
        for ok in ("Mario spara palle di fuoco contro Bowser!", "Un Boo fantasma ci spaventa!", "un abito a righe",
                   "ho 7 anni e il mio numero preferito è 3", "Il Goomba è un nemico"):
            self.assertIsNone(f.check(ok), ok)  # il mondo di Mario resta libero
        self.assertEqual(f.check("Voglio una pistola vera"), ("violenza", "pistol"))
        self.assertEqual(f.check("abito in via Roma 12")[0], "dati_personali")
        self.assertEqual(f.check("chiamami al 347 123 4567"), ("dati_personali", "numero di telefono"))
        self.assertEqual(f.check("la mamma beve la birra")[0], "adulti")
        self.assertIsNone(f.check("il cattivo è morto"))  # "paura" è spento di default
        self.assertEqual(parse_themes("paura, violenza, inventato"), ["violenza", "paura"])
        f.configure(["paura"])
        self.assertEqual(f.check("il cattivo è morto")[0], "paura")
        self.assertIn(THEMES["paura"].label, f.prompt())
        f.configure([])
        self.assertEqual((f.check("pistola"), f.prompt()), (None, ""))

    def make(self, *texts, **kw):
        self.events, self.diary = [], []
        self.backend = SequenceBackend(*texts)
        return AssistantSession(lambda: self.backend, quiet_speaker(), BehaviourEngine(NullActions()),
                                emit=self.events.append, on_message=self.diary.append, **kw)

    def test_blocked_input_and_output(self):
        session = self.make("[gioia] Ciao! Poi prendo la pistola vera e sparo. [gioia] E giochiamo.")
        self.assertIn("Argomenti da evitare sempre", session.build_system_prompt())
        self.assertEqual(session.ask("dimmi dove trovo una pistola"), 0)
        self.assertEqual(self.backend.calls, [])  # non è partito
        self.assertEqual(self.diary[-1]["role"], "system")
        self.assertIn("messaggio bloccato (tema violenza vera, armi e sangue): dimmi dove trovo", self.diary[-1]["content"])
        session.ask("raccontami una storia")
        self.assertTrue(wait_idle(session))
        kinds = [e["kind"] for e in self.events]
        self.assertIn("blocked_output", kinds)
        spoken = [e["text"] for e in self.events if e["kind"] == "sentence"]
        self.assertEqual(spoken, ["Ciao!"])  # la frase con la pistola non è stata detta, né quelle dopo
        self.assertEqual(session.history, [])  # la risposta interrotta non entra nella cronologia
        self.assertIn("risposta di Mario interrotta", self.diary[-1]["content"])
        self.assertIn("pistola", self.diary[-1]["content"])  # i genitori vedono cosa è stato fermato
        self.assertEqual(session.emotion, "pensieroso")

    def test_turn_limit(self):
        from mario.assistant import AssistantBusy
        session = self.make("[gioia] Ciao!", session_turns=2)
        for text in ("uno", "due"):
            session.ask(text)
            self.assertTrue(wait_idle(session))
        with self.assertRaises(AssistantBusy):
            session.ask("tre")
        self.assertIn("messaggi finiti (2 per sessione)", [e["text"] for e in self.events if e["kind"] == "time_up"][0])
        self.assertFalse(session.listening)
        self.assertEqual(session.status()["turns_used"], 2)
        session.reset()  # "Nuova": si ricomincia da capo
        self.assertEqual(session.turns_used, 0)
        session.ask("di nuovo")
        self.assertTrue(wait_idle(session))

    def test_day_summary(self):
        from mario.assistant import report
        t0 = time.mktime((2026, 10, 16, 16, 0, 0, 0, 0, -1))
        msgs = [{"ts": t0, "role": "user", "content": "Ciao Mario", "source": "utente", "emotion": None},
                {"ts": t0 + 5, "role": "assistant", "content": "Ciao!", "source": "anthropic", "emotion": "gioia"},
                {"ts": t0 + 60, "role": "user", "content": "Ho appena fatto un salto!", "source": "gesto: salto"},
                {"ts": t0 + 65, "role": "assistant", "content": "Yahoo!", "source": "anthropic", "emotion": "gioia"},
                {"ts": t0 + 3600, "role": "user", "content": "Facciamo una storia", "source": "voce"},
                {"ts": t0 + 3604, "role": "system", "content": "messaggio bloccato (tema x): ...", "source": "sicurezza"},
                {"ts": t0 + 3610, "role": "assistant", "content": "C'era una volta", "source": "anthropic",
                 "emotion": "calma"}]
        s = report.day_summary(msgs, [{"text": "si chiama Leo"}])
        self.assertEqual((s["child"], s["voice"], s["typed"], s["sensors"], s["answers"]), (2, 1, 1, 1, 3))
        self.assertEqual((s["sessions"], s["minutes"]), (2, 1))  # un'ora di pausa: due partite
        self.assertEqual(s["emotions"], {"gioia": 2, "calma": 1})
        self.assertEqual((len(s["blocked"]), s["memories"], s["sensor_kinds"]), (1, ["si chiama Leo"], {"gesto": 1}))
        text = report.transcript(msgs)
        self.assertIn("16:00 Bambino: Ciao Mario", text)
        self.assertIn("Bambino (a voce): Facciamo una storia", text)
        self.assertIn("Sensore (gesto: salto)", text)
        self.assertIn("Tempo di gioco stimato: 1 minuti in 2 partite", report.summary_request(msgs, s, "2026-10-16")[0]["content"])
        self.assertEqual(report.day_summary([])["first"], None)

    @unittest.skipUnless(HAVE_WEB, "servono flask e bleak (venv dell'host)")
    def test_summary_api_and_settings(self):
        from mario.web import create_app
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(db_path=Path(tmp) / "t.sqlite3", start_worker=False, quiet_assistant=True)
            client, db = app.test_client(), app.extensions["db"]
            client.post("/api/assistant/settings", json={"backend": "demo", "safety_themes": "violenza,paura",
                                                         "session_turns": "30"})
            session = app.extensions["assistant"].session
            self.assertEqual((session.themes.themes, session.session_turns), (["violenza", "paura"], 30))
            now = time.time()
            db.add_assistant_message("user", "Ciao Mario", source="voce", ts=now)
            db.add_assistant_message("assistant", "Ciao!", emotion="gioia", source="demo", ts=now + 2)
            db.add_assistant_message("system", "messaggio bloccato (tema x): pistola", source="sicurezza", ts=now + 3)
            day = time.strftime("%Y-%m-%d")
            r = client.get(f"/api/assistant/summary?day={day}").get_json()
            self.assertEqual((r["summary"]["child"], r["summary"]["voice"], len(r["summary"]["blocked"])), (1, 1, 1))
            self.assertEqual(r["text"], "")
            r = client.post("/api/assistant/summary", json={"day": day, "ai": 1}).get_json()
            self.assertTrue(r["text"])  # il backend demo risponde qualcosa
            self.assertEqual(client.get("/api/assistant/summary?day=2001-01-01").get_json()["summary"]["messages"], 0)
            csv_text = client.get("/api/assistant/history.csv").get_data(as_text=True)
            self.assertIn(";sicurezza;sicurezza;", csv_text)
            self.assertIn(";bambino;voce;", csv_text)


class MemoryEvoTests(unittest.TestCase):
    def setUp(self):
        from mario.web.db import Database
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.sqlite3"
        self.Database = Database

    def tearDown(self):
        self.tmp.cleanup()

    def test_migration_of_old_database(self):
        import sqlite3
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE TABLE assistant_memory (id INTEGER PRIMARY KEY, ts REAL NOT NULL, text TEXT NOT NULL)")
        conn.execute("INSERT INTO assistant_memory (ts, text) VALUES (?, ?)", (time.time(), "si chiama Leo"))
        conn.commit()
        conn.close()
        db = self.Database(self.path)
        self.assertEqual([(m["text"], m["profile"], m["source"], m["expires"]) for m in db.list_memories()],
                         [("si chiama Leo", "", "", None)])  # vecchio ricordo: comune, senza scadenza

    def test_profiles_expiry_and_purge(self):
        from mario.assistant.memory import PARENT, DatabaseMemory, ListMemory
        db = self.Database(self.path)
        for store in (DatabaseMemory(db, "Leo", days=1), ListMemory(profile="Leo", days=1)):
            store.add("ama Yoshi")                       # del modello: scade tra un giorno
            store.add("va a letto alle nove", PARENT)    # del genitore: non scade
            store.profile = ""
            store.add("in casa c'è un gatto", PARENT)    # comune
            store.profile = "Anna"
            store.add("ha paura dei Boo")
            self.assertEqual(sorted(m["text"] for m in store.list()), ["ha paura dei Boo", "in casa c'è un gatto"])
            store.profile = "Leo"
            texts = {m["text"]: m for m in store.list()}
            self.assertEqual(sorted(texts), ["ama Yoshi", "in casa c'è un gatto", "va a letto alle nove"])
            self.assertIsNotNone(texts["ama Yoshi"]["expires"])
            self.assertIsNone(texts["va a letto alle nove"]["expires"])
            self.assertEqual(store.clear(), 2)  # solo i ricordi di Leo
            store.profile = "Anna"
            self.assertEqual(len(store.list()), 2)
        later = time.time() + 2 * 86400
        self.assertEqual([m["text"] for m in db.list_memories(profile="Anna", now=later)], ["in casa c'è un gatto"])
        self.assertEqual(db.purge_memories(later), 1)  # "ha paura dei Boo" era del modello

    def test_parse_summary(self):
        from mario.assistant.memory import parse_summary, summary_candidates
        text = "1. Il bambino si chiama Leo.\n- Ama Yoshi [gioia]\n• ama yoshi\n\n  ok\n2) Ha sette anni"
        self.assertEqual(parse_summary(text), ["Il bambino si chiama Leo", "Ama Yoshi", "Ha sette anni"])
        self.assertEqual(len(parse_summary("\n".join(f"frase {i}" for i in range(30)))), 10)
        items = [{"text": "a", "profile": "Leo", "source": "modello"}, {"text": "b", "profile": "Leo", "source": "genitore"},
                 {"text": "c", "profile": "", "source": "modello"}]
        self.assertEqual([i["text"] for i in summary_candidates(items, "Leo")], ["a"])

    def test_automatic_summary(self):
        from mario.assistant.memory import PARENT, ListMemory
        memory = ListMemory(profile="Leo")
        memory.add("va a letto alle nove", PARENT)
        for i in range(20):
            memory.add(f"ricordo numero {i}")
        events = []
        backend = SequenceBackend("[gioia] Che bello! [ricorda: ama i Goomba]",
                                  "Si chiama Leo\nAma i Goomba\nHa sette anni")
        session = AssistantSession(lambda: backend, quiet_speaker(), BehaviourEngine(NullActions()),
                                   emit=events.append, memory=memory)
        self.assertIn("Adesso stai giocando con Leo", session.build_system_prompt())
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        deadline = time.time() + 5
        while not any(e["kind"] == "memory_summarized" for e in events) and time.time() < deadline:
            time.sleep(0.02)
        done = [e for e in events if e["kind"] == "memory_summarized"]
        self.assertEqual((done[0]["before"], done[0]["after"]), (21, 3))
        self.assertEqual(sorted((m["text"], m["source"]) for m in memory.list()),
                         [("Ama i Goomba", "riassunto"), ("Ha sette anni", "riassunto"), ("Si chiama Leo", "riassunto"),
                          ("va a letto alle nove", "genitore")])
        self.assertIn("Ricordi, dal più vecchio", backend.calls[-1])
        session.set_profile("Anna, 6 anni")  # le virgole non servono nei nomi
        self.assertEqual((session.profile, memory.profile), ("Anna 6 anni", "Anna 6 anni"))
        self.assertEqual([m["text"] for m in memory.list()], [])

    def test_bad_summary_keeps_memories(self):
        from mario.assistant.memory import ListMemory
        memory = ListMemory()
        for i in range(21):
            memory.add(f"ricordo {i}")
        events = []
        session = AssistantSession(lambda: SequenceBackend("ok"), quiet_speaker(), BehaviourEngine(NullActions()),
                                   emit=events.append, memory=memory)
        session._maybe_summarize()
        deadline = time.time() + 5
        while session._summarizing and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(len(memory.list(100)), 21)
        self.assertIn("riassunto dei ricordi non riuscito", events[-1]["text"])

    @unittest.skipUnless(HAVE_WEB, "servono flask e bleak (venv dell'host)")
    def test_profile_api(self):
        from mario.web import create_app
        app = create_app(db_path=self.path, start_worker=False, quiet_assistant=True)
        client = app.test_client()
        r = client.post("/api/assistant/profile", json={"profile": "Leo"}).get_json()
        self.assertEqual((r["profile"], r["profiles"]), ("Leo", ["Leo"]))
        client.post("/api/assistant/memory", json={"text": "ama Yoshi"})
        client.post("/api/assistant/memory", json={"text": "in casa c'è un gatto", "profile": ""})
        client.post("/api/assistant/profile", json={"profile": "Anna"})
        r = client.get("/api/assistant/memory").get_json()
        self.assertEqual(([m["text"] for m in r["memory"]], r["profiles"]), (["in casa c'è un gatto"], ["Leo", "Anna"]))
        everything = client.get("/api/assistant/memory?all=1").get_json()["memory"]
        self.assertEqual(sorted((m["text"], m["profile"], m["source"]) for m in everything),
                         [("ama Yoshi", "Leo", "genitore"), ("in casa c'è un gatto", "", "genitore")])
        s = client.post("/api/assistant/settings", json={"memory_days": "30"}).get_json()["settings"]
        self.assertEqual((s["memory_days"], s["profile"]), ("30", "Anna"))
        self.assertEqual(app.extensions["assistant"].session.memory.days, 30.0)

if __name__ == "__main__":
    unittest.main()
