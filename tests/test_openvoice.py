"""Voce IA (piper + OpenVoice): protocollo del processo, client con un processo finto, motore `openvoice` di
Speaker con preparazione in anticipo e voce di riserva. Nessun modello vero: girano ovunque."""
import io
import json
import sys
import tempfile
import textwrap
import time
import unittest
import wave
from pathlib import Path

from mario.assistant import openvoice as ov
from mario.assistant import openvoice_worker as worker
from mario.assistant.tts import Speaker

PROJECT = Path(__file__).resolve().parent.parent


def write_wav(path, seconds=0.1, value=1000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(int(value).to_bytes(2, "little", signed=True) * int(22050 * seconds))


class FakeEngine:
    def __init__(self, fail_load=False):
        self.fail_load = fail_load
        self.calls = []

    def load(self):
        if self.fail_load:
            raise FileNotFoundError("modello mancante")
        return {"openvoice": True, "watermark": False}

    def speak(self, text, out, length_scale, tau, gain):
        self.calls.append((text, length_scale, tau, gain))
        write_wav(out)
        return {"base_s": 0.01, "convert_s": 0.02, "audio_s": 0.1}

    def set_reference(self, path):
        if not Path(path).is_file():
            raise FileNotFoundError(path)


FAKE_WORKER = textwrap.dedent('''
    import sys, time, wave
    sys.path.insert(0, {project!r})
    from mario.assistant.openvoice_worker import serve

    class Engine:
        def load(self):
            if "--fail" in sys.argv:
                raise RuntimeError("torch mancante")
            time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1][0].isdigit() else 0)
            return {{"openvoice": True, "watermark": False}}

        def speak(self, text, out, length_scale, tau, gain):
            if text == "blocca":
                time.sleep(30)
            if text == "muori":
                sys.exit(3)
            with wave.open(out, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(b"\\0\\0" * 2205)
            return {{"base_s": 0.01, "convert_s": 0.02, "audio_s": 0.1}}

        def set_reference(self, path):
            pass

    sys.exit(serve(Engine(), sys.stdin, sys.stdout))
''')


class WorkerProtocolTests(unittest.TestCase):
    def run_serve(self, engine, lines):
        out = io.StringIO()
        code = worker.serve(engine, io.StringIO("".join(json.dumps(x) + "\n" if isinstance(x, dict) else x for x in lines)), out)
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_ready_speak_errors_quit(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = FakeEngine()
            code, replies = self.run_serve(engine, [
                {"id": 1, "cmd": "speak", "text": "Ciao!", "out": f"{tmp}/a.wav", "length_scale": 0.9, "tau": 0.4},
                {"id": 2, "cmd": "reference", "path": f"{tmp}/manca.wav"},
                {"id": 3, "cmd": "boh"},
                "non json\n",
                {"id": 4, "cmd": "ping"},
                {"cmd": "quit"},
                {"id": 5, "cmd": "ping"},  # dopo quit: ignorato
            ])
            self.assertEqual(code, 0)
            self.assertEqual(replies[0]["event"], "ready")
            self.assertTrue(replies[0]["openvoice"])
            self.assertEqual((replies[1]["id"], replies[1]["ok"], replies[1]["convert_s"]), (1, True, 0.02))
            self.assertEqual(engine.calls, [("Ciao!", 0.9, 0.4, 1.0)])
            self.assertTrue(Path(f"{tmp}/a.wav").is_file())
            self.assertFalse(replies[2]["ok"])
            self.assertIn("FileNotFoundError", replies[2]["error"])
            self.assertIn("comando sconosciuto", replies[3]["error"])
            self.assertEqual(replies[4], {"ok": False, "error": "richiesta non JSON"})
            self.assertEqual(replies[5], {"id": 4, "ok": True})
            self.assertEqual(len(replies), 6)

    def test_load_failure_reports_error(self):
        code, replies = self.run_serve(FakeEngine(fail_load=True), [])
        self.assertEqual(code, 1)
        self.assertEqual(replies, [{"event": "error", "error": "FileNotFoundError: modello mancante"}])

    def test_gain_and_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.wav"
            write_wav(path, seconds=0.5, value=20000)
            self.assertAlmostEqual(worker.wav_seconds(path), 0.5, places=2)
            worker.apply_gain(path, 2.0)  # 40000 non entra in 16 bit: tagliato a 32767
            with wave.open(str(path), "rb") as w:
                self.assertEqual(int.from_bytes(w.readframes(1), "little", signed=True), 32767)
            self.assertEqual(worker.wav_seconds(Path(tmp) / "manca.wav"), 0.0)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.script = self.dir / "fake_worker.py"
        self.script.write_text(FAKE_WORKER.format(project=str(PROJECT)), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, *args):
        return ov.OpenVoiceClient("rif.wav", "voce.onnx", command=[sys.executable, str(self.script), *args])

    def test_start_speak_timeout_restart_and_crash(self):
        c = self.client("0.2")
        c.start()
        self.assertEqual(c.state, "in avvio")
        self.assertTrue(c.wait_ready(10))
        out = self.dir / "a.wav"
        reply = c.speak("Ciao", str(out), timeout=10)
        self.assertEqual(reply["convert_s"], 0.02)
        self.assertTrue(out.is_file())
        self.assertEqual(c.status()["conversions"], 1)
        with self.assertRaises(ov.OpenVoiceError) as ctx:
            c.speak("blocca", str(self.dir / "b.wav"), timeout=0.5)
        self.assertIn("riavviato", str(ctx.exception))
        self.assertTrue(c.wait_ready(10))  # ripartito da solo
        c.speak("di nuovo", str(self.dir / "c.wav"), timeout=10)
        with self.assertRaises(ov.OpenVoiceError):
            c.speak("muori", str(self.dir / "d.wav"), timeout=10)
        deadline = time.time() + 5
        while c.state != "errore" and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(c.state, "errore")
        self.assertIn("terminato", c.last_error)
        with self.assertRaises(ov.OpenVoiceError):
            c.speak("dopo", str(self.dir / "e.wav"))
        self.assertTrue(c.revive())  # aveva già caricato i modelli: riparte
        self.assertTrue(c.wait_ready(10))
        c.speak("ripartito", str(self.dir / "f.wav"), timeout=10)
        self.assertEqual(c.status()["restarts"], 1)
        c.stop()

    def test_argv_modes(self):
        argv = ov.OpenVoiceClient("", "voce.onnx")._argv()
        self.assertEqual(argv[1:3], ["-I", str(ov.WORKER)])  # isolato: openvoice.py non nasconde il pacchetto
        self.assertNotIn("--converter", argv)                 # senza riferimento: solo piper
        c = ov.OpenVoiceClient("rif.wav", "voce.onnx", watermark=False, threads=2)
        argv = c._argv()
        self.assertEqual(argv[argv.index("--reference") + 1], "rif.wav")
        self.assertIn("--no-watermark", argv)
        self.assertEqual(argv[argv.index("--threads") + 1], "2")
        self.assertEqual((c.status()["mode"], ov.OpenVoiceClient("", "v").status()["mode"]), ("openvoice", "piper"))

    def test_load_error_and_missing_python(self):
        c = self.client("--fail")
        c.start()
        self.assertFalse(c.wait_ready(10))
        self.assertIn("torch mancante", c.last_error)
        self.assertFalse(c.revive())  # errore di caricamento: ripartire non servirebbe
        c.stop()
        c2 = ov.OpenVoiceClient("rif.wav", "voce.onnx", python=self.dir / "nessun-python")
        c2.start()
        self.assertFalse(c2.wait_ready(1))
        self.assertIn("avvio del processo OpenVoice fallito", c2.last_error)

    def test_problem_messages(self):
        ref = self.dir / "rif.wav"
        write_wav(ref)
        self.assertEqual(ov.problem(""), "nessuna registrazione di riferimento")
        self.assertIn("non trovata", ov.problem(str(self.dir / "manca.wav")))
        self.assertIn("ambiente OpenVoice assente", ov.problem(str(ref), python=self.dir / "py"))
        self.assertIn("checkpoint", ov.problem(str(ref), python=Path(sys.executable), converter=self.dir))
        conv = self.dir / "conv"
        conv.mkdir()
        (conv / "config.json").write_text("{}")
        (conv / "checkpoint.pth").write_bytes(b"x")
        model = self.dir / "voce.onnx"
        model.write_bytes(b"x")
        self.assertEqual(ov.problem(str(ref), str(model), python=Path(sys.executable), converter=conv), "")
        self.assertIn("voce piper", ov.problem(str(ref), str(self.dir / "manca.onnx"), python=Path(sys.executable),
                                               converter=conv))


class SpeakerOpenVoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        script = self.dir / "fake_worker.py"
        script.write_text(FAKE_WORKER.format(project=str(PROJECT)), encoding="utf-8")
        self.played = self.dir / "suonati.txt"
        fallback_log = self.dir / "riserva.txt"

        class FakeSpeaker(Speaker):
            openvoice_problem = staticmethod(lambda ref, model="": "")

            def openvoice_factory(self_, reference, piper_model="", watermark=True):  # noqa: N805
                return ov.OpenVoiceClient(reference, piper_model or "voce.onnx",
                                          command=[sys.executable, str(script), *self.worker_args])

            def _render_with(self_, engine, model, text, emotion, serial, timing):  # noqa: N805
                timing.setdefault("engine", engine)
                with open(fallback_log, "a", encoding="utf-8") as fh:
                    fh.write(text + "\n")
                return "cmd", [sys.executable, "-c", "pass"]

        self.Speaker = FakeSpeaker
        self.fallback_log = fallback_log
        self.worker_args = []

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kw):
        sp = self.Speaker(enabled=True, openvoice_reference="rif.wav", sounds=False, **kw)
        # lettore finto: annota il file suonato (che poi viene cancellato)
        sp.player = [sys.executable, "-c",
                     f"import sys, shutil; open({str(self.played)!r}, 'a').write(sys.argv[1] + chr(10))"]
        return sp

    def test_sentences_rendered_ahead_and_played_in_order(self):
        sp = self.make()
        self.assertEqual(sp.engine, "openvoice")
        started = []
        sp.on_start = lambda text, emo: started.append(text)
        for text in ("Prima frase.", "Seconda frase.", "Terza frase."):
            sp.speak(text, "gioia")
        self.assertFalse(sp.idle)
        self.assertTrue(sp.wait(20))
        self.assertEqual(started, ["Prima frase.", "Seconda frase.", "Terza frase."])
        played = self.played.read_text().split()
        self.assertEqual(len(played), 3)
        self.assertTrue(all(Path(p).name.startswith("tts-") for p in played))
        self.assertFalse(any(Path(p).exists() for p in played))  # file cancellati dopo la riproduzione
        st = sp.status()
        self.assertEqual(st["openvoice"]["state"], "pronto")
        self.assertEqual(st["last_timing"]["engine"], "openvoice")
        self.assertEqual(st["last_timing"]["convert_s"], 0.02)
        self.assertFalse(self.fallback_log.exists())
        sp.close()
        self.assertEqual(sp.status()["openvoice"]["state"], "spento")

    def test_fallback_when_worker_fails(self):
        self.worker_args = ["--fail"]
        sp = self.make()
        sp.speak("Ciao lo stesso!", "calma")
        self.assertTrue(sp.wait(20))
        self.assertEqual(self.fallback_log.read_text().split("\n")[0], "Ciao lo stesso!")
        self.assertIn("voce IA", sp.last_error)
        self.assertIn("torch mancante", sp.last_error)
        self.assertEqual(sp.status()["last_timing"]["fallback"], sp._fallback()[0])
        sp.close()

    def test_slow_start_uses_fallback_then_ai_voice(self):
        self.worker_args = ["1.0"]  # il processo impiega 1 s a caricare
        sp = self.make(openvoice_wait=0.1)
        sp.speak("Subito.", None)  # non aspetta oltre 0,1 s: voce di riserva
        self.assertTrue(sp.wait(20))
        self.assertEqual(self.fallback_log.read_text().strip(), "Subito.")
        self.assertTrue(sp._ov.wait_ready(10))
        sp.speak("Adesso con la voce IA.", None)
        self.assertTrue(sp.wait(20))
        self.assertEqual(sp.status()["last_timing"]["engine"], "openvoice")
        sp.close()

    def test_stop_discards_prepared_sentences(self):
        self.worker_args = ["0.5"]
        sp = self.make()
        for i in range(5):
            sp.speak(f"Frase {i}.", None)
        sp.stop()
        self.assertTrue(sp.wait(10))
        self.assertTrue(sp.idle)
        sp.close()

    def test_piper_engine_uses_persistent_worker(self):
        model = self.dir / "voce.onnx"
        model.write_bytes(b"x")
        saved = ov.DEFAULT_PYTHON
        ov.DEFAULT_PYTHON = Path(sys.executable)  # come se .venv-openvoice ci fosse
        try:
            sp = self.Speaker(enabled=True, engine="piper", piper_model=str(model), sounds=False)
        finally:
            ov.DEFAULT_PYTHON = saved
        sp.player = [sys.executable, "-c", "pass"]
        self.assertEqual(sp.status()["openvoice"]["mode"], "piper")
        sp.speak("Veloce.", "gioia")
        self.assertTrue(sp.wait(20))
        self.assertEqual(sp.status()["last_timing"]["engine"], "piper")
        self.assertNotIn("fallback", sp.status()["last_timing"])
        sp.close()

    def test_configure_switches_engine(self):
        sp = Speaker(enabled=False, engine="spd-say")
        sp.configure(openvoice_reference=str(self.dir / "manca.wav"))
        self.assertNotEqual(sp.engine, "openvoice")
        self.assertIn("non trovata", sp.status()["openvoice"]["problem"])
        sp.configure(openvoice_tau=5)
        self.assertEqual(sp.openvoice_tau, 1.0)
        self.assertEqual(sp._length_scale("gioia"), 0.93)
        sp.configure(volume=-50)
        self.assertEqual(sp._gain(), 0.5)


if __name__ == "__main__":
    unittest.main()
