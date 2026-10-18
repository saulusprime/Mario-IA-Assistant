"""Attivazione con la voce ("Ehi Mario"): motore del processo di ascolto con un riconoscitore finto e audio
sintetico, verifica della trascrizione, processo finto con il protocollo vero, sessione dell'assistente.
Senza Vosk, Whisper né microfono."""
import importlib.util
import json
import math
import struct
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

from mario.assistant import AssistantSession, Speaker
from mario.assistant import stt
from mario.assistant import wake_worker as ww
from mario.assistant.behaviours import BehaviourEngine, NullActions

FRAME = ww.FRAME_BYTES  # 20 ms


def silence(seconds):
    return b"\0" * (int(seconds * 50) * FRAME)


def voice(seconds, amplitude=8000):
    n = int(seconds * 50) * ww.FRAME
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(n)))


class ScriptedRecognizer:
    """Al pezzo numero N (da 100 ms) risponde quello che dice il copione: {N: (finale, testo)}."""

    def __init__(self, script):
        self.script = script
        self.n = 0
        self.resets = 0

    def accept(self, pcm):
        self.n += 1
        return self.script.get(self.n, (False, ""))

    def reset(self):
        self.resets += 1


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events = []

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self, script, **kw):
        return ww.WakeEngine(ScriptedRecognizer(script), ["ehi mario"], Path(self.tmp.name), self.events.append, **kw)

    def kinds(self):
        return [e["event"] for e in self.events]

    def test_partial_call_records_from_the_start_of_the_phrase(self):
        # "Ehi Mario, raccontami una storia" tutto d'un fiato: Vosk se ne accorge a metà (pezzo 12, 1,2 s)
        eng = self.engine({12: (False, "ehi mario")})
        eng.feed(silence(0.5) + voice(2.0) + silence(1.5))
        self.assertEqual(self.kinds(), ["wake", "utterance"])
        utt = self.events[-1]
        self.assertEqual((utt["mode"], utt["reason"]), ("wake", "silenzio"))
        with wave.open(utt["path"]) as w:
            seconds = w.getnframes() / 16000
        # dall'inizio della frase (0,5 s) meno 0,3 s di margine fino a un secondo di silenzio dopo la fine (3,5 s)
        self.assertAlmostEqual(utt["start_s"], 0.2, delta=0.05)
        self.assertAlmostEqual(seconds, 3.3, delta=0.1)
        self.assertEqual(eng.mode, "ascolto")

    def test_final_call_keeps_only_its_phrase(self):
        # un'altra frase, poi "hey mario" che Vosk chiude con un risultato finale 0,4 s dopo la fine
        eng = self.engine({8: (True, "ehi maria"), 30: (True, "hey mario")})
        eng.feed(voice(0.8) + silence(1.0) + voice(0.8) + silence(2.0))
        self.assertEqual(self.kinds(), ["wake", "utterance"])
        self.assertEqual((self.events[0]["text"], self.events[0]["at_s"]), ("hey mario", 3.0))
        utt = self.events[-1]
        self.assertAlmostEqual(utt["start_s"], 1.5, delta=0.05)  # la frase precedente non c'è
        with wave.open(utt["path"]) as w:  # i 0,4 s di silenzio già passati contano: ne mancano 0,6
            self.assertAlmostEqual(w.getnframes() / 16000, 2.1, delta=0.1)

    def test_pause_drops_audio_and_cancels_a_recording(self):
        eng = self.engine({3: (False, "ehi mario")})
        eng.feed(voice(0.5))
        self.assertEqual(eng.mode, "registrazione")
        eng.pause()
        self.assertEqual(self.kinds(), ["wake", "cancelled"])
        eng.feed(voice(3.0) + silence(2.0))  # Mario parla dagli altoparlanti: niente arriva a Vosk
        self.assertEqual(eng.rec.n, 3)
        eng.resume()
        self.assertFalse(eng.paused)
        self.assertEqual(self.kinds(), ["wake", "cancelled"])

    def test_listen_without_wake_word(self):
        eng = self.engine({})
        eng.listen(wait_s=1.0)
        eng.feed(silence(1.5))
        self.assertEqual(self.events[-1], {"event": "no_speech", "mode": "listen"})
        eng.listen(wait_s=3.0)
        eng.feed(silence(0.5) + voice(1.0) + silence(1.2))
        self.assertEqual(self.events[-1]["event"], "utterance")
        self.assertEqual(self.events[-1]["mode"], "listen")

    def test_maximum_length(self):
        eng = self.engine({2: (False, "ehi mario")}, max_s=2.0)
        eng.feed(voice(5.0))
        self.assertEqual(self.events[-1]["reason"], "tempo massimo")

    def test_vad_follows_background_noise(self):
        vad = ww.Vad()
        hum = voice(0.02, amplitude=400)  # ronzio costante, poco sopra la soglia minima
        for _ in range(200):
            vad.is_speech(hum)
        self.assertFalse(vad.is_speech(hum))
        self.assertTrue(vad.is_speech(voice(0.02, amplitude=6000)))
        self.assertFalse(vad.is_speech(silence(0.02)))

    def test_grammar_and_pattern(self):
        grammar = ww.grammar_for(["ehi mario"])
        for word in ("ehi mario", "hey mario", "maria", "dario", "sono mario", "[unk]"):
            self.assertIn(word, grammar)
        self.assertEqual(grammar[-1], "[unk]")
        pattern = ww.phrase_pattern(["ehi mario"])
        self.assertTrue(pattern.search(ww.normalize("Hey, Mario!")))
        self.assertTrue(pattern.search("e mario"))  # inizio della frase: un "ehi" detto in fretta
        self.assertFalse(pattern.search("luigi e mario"))
        self.assertFalse(pattern.search("ehi maria"))
        self.assertFalse(pattern.search("ehi mariolino"))


class SplitWakeTests(unittest.TestCase):
    def test_transcripts_from_the_tests_of_18_10(self):
        calls = {"Ehi Mario, raccontami una storia.": "Raccontami una storia.", "E Mario.": "", "Ai, Mario!": "",
                 "È Mario, mi senti?": "Mi senti?", "Hai Mario, mi senti?": "Mi senti?",
                 "Eh Mario, come si chiama tuo fratello?": "Come si chiama tuo fratello?",
                 "Allora... ehi Mario! Ciao": "Ciao"}
        for text, rest in calls.items():
            self.assertEqual(stt.split_wake(text), (True, rest), text)
        for text in ("Hey, Maria, dove sei?", "Ciao Mario, come stai?", "Hai visto il film di Mario?",
                     "Luigi e Mario giocano.", "Mario, vieni a giocare con me.", "Ehi, Marco, vieni qui.", ""):
            self.assertEqual(stt.split_wake(text), (False, ""), text)

    def test_phrases_setting(self):
        self.assertEqual(stt.parse_wake_phrases(" Ehi Mario! ; ciao mario,"), ["ehi mario", "ciao mario"])
        self.assertEqual(stt.parse_wake_phrases(""), list(stt.DEFAULT_WAKE_PHRASES))
        self.assertEqual(stt.split_wake("Ciao Mario, giochiamo?", ["ciao mario"]), (True, "Giochiamo?"))
        self.assertEqual(stt.split_wake("E Mario, giochiamo?", ["ciao mario"]), (False, ""))


FAKE_WAKE = """
import json, sys, wave
out_dir = sys.argv[1]
def send(msg):
    print(json.dumps(msg), flush=True)
send({"event": "ready", "load_s": 0.01, "phrases": ["ehi mario"], "source": "finto"})
for line in sys.stdin:
    req = json.loads(line)
    if req.get("cmd") == "quit":
        break
    if req.get("cmd") == "listen":  # finge che qualcuno abbia parlato
        path = out_dir + "/ascolto.wav"
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\\0\\1" * 16000)
        send({"id": req["id"], "ok": True, "mode": "attesa", "paused": False})
        send({"event": "utterance", "path": path, "audio_s": 1.0, "mode": "listen", "reason": "silenzio"})
        continue
    send({"id": req["id"], "ok": True, "cmd": req.get("cmd")})
    with open(out_dir + "/comandi.txt", "a") as f:
        f.write(req.get("cmd") + "\\n")
"""

FAKE_STT = """
import json, os, sys, wave
print(json.dumps({"event": "ready", "load_s": 0.01, "model": "finto", "threads": 1}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    if req.get("cmd") == "quit":
        break
    if req.get("cmd") == "transcribe":
        with wave.open(req["path"]) as w:
            secs = w.getnframes() / w.getframerate()
        side = req["path"] + ".txt"
        text = open(side, encoding="utf-8").read() if os.path.exists(side) else "Facciamo un salto"
        print(json.dumps({"id": req["id"], "ok": True, "text": text, "audio_s": round(secs, 2),
                          "speech_s": round(secs, 2), "elapsed_s": 0.01}), flush=True)
"""


class SequenceBackend:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.model = "copione"

    def describe(self):
        return self.model

    def stream(self, system, messages):
        self.calls.append(messages[-1]["content"])
        yield self.answers.pop(0) if self.answers else "[gioia] Ok"


class FakeWake:
    def __init__(self):
        self.phrases = ["ehi mario", "hey mario"]
        self.counts = {"wake": 0, "utterance": 0, "accepted": 0, "rejected": 0}
        self.on_event = None
        self.gate = None
        self.listens = []
        self.closed = False

    def set_gate(self, gate, interval=0.1):
        self.gate = gate

    def listen(self, wait_s=6.0):
        self.listens.append(wait_s)
        return True

    def status(self):
        return {"state": "pronto", "phrases": self.phrases, "paused": False, **self.counts}

    def close(self):
        self.closed = True


class ListenerAndSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "stt.py").write_text(FAKE_STT, encoding="utf-8")
        (self.dir / "wake.py").write_text(FAKE_WAKE, encoding="utf-8")
        self._tmp_dir = stt.TMP_DIR
        stt.TMP_DIR = self.dir / "registrazioni"
        self.events = []

    def tearDown(self):
        stt.TMP_DIR = self._tmp_dir
        self.tmp.cleanup()

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def kinds(self):
        return [e["kind"] for e in self.events]

    def session(self, speaker=None):
        self.backend = SequenceBackend("[gioia] C'era una volta…")
        client = stt.SpeechClient(command=[sys.executable, str(self.dir / "stt.py")])
        self.vi = stt.VoiceInput(client=client, recorder=stt.Recorder(name="finto", command=["true"]))
        self.wake = FakeWake()
        return AssistantSession(lambda: self.backend, speaker or Speaker(enabled=False, engine=None, sounds=False),
                                BehaviourEngine(NullActions()), emit=self.events.append, voice_input=self.vi,
                                wake=self.wake)

    def utterance(self, session, text, mode="wake"):
        path = self.dir / f"frase-{len(self.events)}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0\1" * 24000)
        Path(str(path) + ".txt").write_text(text, encoding="utf-8")
        self.wake.on_event({"event": "utterance", "path": str(path), "audio_s": 1.5, "mode": mode})
        return path

    def test_call_with_question(self):
        session = self.session()
        self.assertIs(self.wake.on_event.__self__, session)
        path = self.utterance(session, "Ehi Mario, raccontami una storia.")
        self.assertTrue(self.wait_for(lambda: self.backend.calls))
        self.assertIn("(a voce) Raccontami una storia.", self.backend.calls[0])
        self.assertEqual([e["text"] for e in self.events if e["kind"] == "voice_text"], ["Raccontami una storia."])
        self.assertFalse(path.exists())  # l'audio non resta
        self.assertEqual(self.wake.counts["accepted"], 1)
        self.assertEqual(session.status()["wake"]["accepted"], 1)
        session.shutdown()
        self.assertTrue(self.wake.closed)

    def test_overheard_phrase_leaves_no_trace(self):
        session = self.session()
        self.utterance(session, "Ehi Maria, dove sei? Il numero è 333 1234567")
        self.assertTrue(self.wait_for(lambda: "voice_wake_rejected" in self.kinds()))
        time.sleep(0.1)
        self.assertEqual(self.backend.calls, [])
        self.assertNotIn("Maria", json.dumps(self.events))   # né nella pagina, né nel diario
        self.assertNotIn("Maria", json.dumps(session.status()))
        self.assertTrue(self.vi.last.get("private"))
        self.assertEqual(self.wake.counts["rejected"], 1)
        session.shutdown()

    def test_bare_call_then_question_without_wake_word(self):
        session = self.session()
        self.utterance(session, "Ehi Mario!")
        self.assertTrue(self.wait_for(lambda: self.wake.listens))
        self.assertEqual(self.wake.listens, [6.0])
        self.assertIn("Eccomi", " ".join(e["text"] for e in self.events if e["kind"] == "voice_wake"))
        self.assertEqual(self.backend.calls, [])
        self.utterance(session, "Facciamo un salto", mode="listen")  # dopo "Eccomi!" non serve richiamarlo
        self.assertTrue(self.wait_for(lambda: self.backend.calls))
        self.assertIn("(a voce) Facciamo un salto", self.backend.calls[0])
        session.shutdown()

    def test_gate_closed_while_mario_speaks(self):
        class SilentSpeaker(Speaker):
            def _run(self):  # le frasi restano in coda: Mario "sta parlando"
                pass
        speaker = SilentSpeaker(enabled=True, engine="spd-say")
        session = self.session(speaker)
        self.assertTrue(session.wake_allowed())
        speaker.speak("Ciao, sono Mario!", "gioia")
        self.assertFalse(session.wake_allowed())  # sentirebbe il proprio nome dagli altoparlanti
        speaker.stop()
        self.assertFalse(session.wake_allowed())  # coda degli altoparlanti
        session._speaker_busy_at -= 1.0
        self.assertTrue(session.wake_allowed())
        session.time_up = True
        self.assertFalse(session.wake_allowed())
        session.shutdown()

    def test_listener_process_protocol_and_gate(self):
        got = []
        listener = stt.WakeListener(["Ehi, Mario"], command=[sys.executable, str(self.dir / "wake.py"), str(self.dir)],
                                    on_event=got.append)
        self.assertEqual(listener.phrases, ["ehi mario"])
        self.assertEqual(listener.problem(), "")
        listener.start()
        try:
            self.assertTrue(listener.wait_ready(5))
            allowed = threading.Event()
            listener.set_gate(allowed.is_set, interval=0.02)
            self.assertTrue(self.wait_for(lambda: listener.paused))  # cancello chiuso: pausa
            allowed.set()
            self.assertTrue(self.wait_for(lambda: not listener.paused))
            self.assertTrue(listener.listen(3.0))
            self.assertTrue(self.wait_for(lambda: got))
            self.assertEqual((got[0]["event"], got[0]["mode"]), ("utterance", "listen"))
            self.assertEqual(listener.counts["utterance"], 1)
            commands = (self.dir / "comandi.txt").read_text().split()
            self.assertEqual(commands[:2], ["pause", "resume"])
        finally:
            listener.close()
        self.assertEqual(listener.state, "spento")

    @unittest.skipUnless(importlib.util.find_spec("flask") and importlib.util.find_spec("bleak"),
                         "servono flask e bleak (venv dell'host)")
    def test_dashboard_settings(self):
        from mario.web import create_app
        app = create_app(db_path=self.dir / "t.sqlite3", start_worker=False, quiet_assistant=True)
        client = app.test_client()
        s = client.get("/api/assistant/settings").get_json()["settings"]
        self.assertEqual((s["wake_word"], s["wake_phrases"]), (False, "ehi mario, hey mario"))
        s = client.post("/api/assistant/settings", json={"voice_input": 1, "wake_word": 1,
                                                         "wake_phrases": "ciao mario"}).get_json()["settings"]
        self.assertEqual((s["wake_word"], s["wake_phrases"]), (True, "ciao mario"))
        session = app.extensions["assistant"].session
        self.assertIsNone(session.wake)  # silenzioso (test): niente microfono sempre aperto
        self.assertIsNone(client.get("/api/assistant/status").get_json()["status"]["wake"])

    def test_argv_and_problems(self):
        listener = stt.WakeListener("ehi mario", recorder="pw-record")
        argv = listener._argv()
        self.assertEqual(argv[1:3], ["-I", str(stt.WAKE_WORKER)])
        self.assertEqual(argv[argv.index("--phrases") + 1], "ehi mario")
        cmd = json.loads(argv[argv.index("--recorder-cmd") + 1])
        self.assertEqual(cmd[0], "pw-record")
        self.assertIn("--raw", cmd)
        self.assertEqual(cmd[-1], "-")  # PCM grezzo sullo standard output
        self.assertIn("setup_stt.sh", stt.wake_problem(python=self.dir / "manca"))
        on_file = stt.WakeListener(source_file=self.dir / "x.wav")
        self.assertEqual(on_file._argv()[-2:], ["--file", str(self.dir / "x.wav")])


if __name__ == "__main__":
    unittest.main()
