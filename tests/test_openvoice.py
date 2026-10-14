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


class SoundPackTests(unittest.TestCase):
    """Suoni veri dei giochi: catalogo, stili, scelte dell'utente, file propri, ricadute (archivio finto)."""

    def setUp(self):
        from mario.assistant import soundpack
        self.sp = soundpack
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dir = self.root / "tmk"
        files = {("smb", "Coin"): "smb/smb_coin.wav", ("sm64", "\"Yahoo!\""): "sm64/sm64_yahoo.wav",
                 ("smb", "Power-up"): "smb/smb_powerup.wav", ("sm64", "Bowser's laugh"): "sm64/sm64_bowser_laugh.wav"}
        items = []
        for (game, desc), rel in files.items():
            (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
            write_wav(self.dir / rel)
            items.append({"section": "wav", "game": game, "category": "SFX", "description": desc, "file": rel,
                          "url": "x", "size": 1})
        items.append({"game": "smb", "category": "SFX", "description": "Stomp", "file": "smb/manca.wav", "url": "x", "size": 1})
        (self.dir / "mid" / "smb").mkdir(parents=True)
        (self.dir / "mid" / "smb" / "smb_over.mid").write_bytes(b"MThd")
        items.append({"section": "mid", "game": "smb", "category": "Super Mario Bros.", "description": "Overworld",
                      "file": "mid/smb/smb_over.mid", "url": "x", "size": 4})
        (self.dir / "catalogo.json").write_text(json.dumps({"items": items}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def pack(self):
        return self.sp.SoundPack(self.root)

    def test_styles_and_fallbacks(self):
        pack = self.pack()
        self.assertTrue(pack.available)
        self.assertEqual(len(pack.items), 5)  # il file mancante non conta
        self.assertEqual(pack.path("coin", "giochi").name, "smb_coin.wav")
        self.assertEqual(pack.path("coin", "voce").name, "sm64_yahoo.wav")     # esclamazione di Mario
        self.assertEqual(pack.path("powerup", "voce").name, "smb_powerup.wav")  # nessuna voce: effetto del gioco
        self.assertIsNone(pack.path("coin", "sintetici"))
        self.assertIsNone(pack.path("stomp", "giochi"))                         # file non scaricato
        self.assertEqual(pack.path("sm64/sm64_bowser_laugh.wav").name, "sm64_bowser_laugh.wav")  # file preciso
        self.assertEqual(pack.path("tmk/sm64/sm64_bowser_laugh.wav").name, "sm64_bowser_laugh.wav")
        for bad in ("../catalogo.json", "sm64/../../x.wav", "/etc/passwd", "tmk/../../x"):
            self.assertIsNone(pack.path(bad), bad)
            self.assertIsNone(pack.resolve(bad), bad)
        names = pack.names()
        self.assertEqual((names["giochi"], names["voce"]), (["coin", "powerup"], ["coin", "wahoo"]))  # wahoo: anche "Yahoo!"
        st = pack.status()
        self.assertEqual(st["games"], {"smb": 3, "sm64": 2})
        self.assertIn("stomp", st["missing"])
        self.assertFalse(self.sp.SoundPack(self.root / "vuota").available)

    def test_user_assignments_volume_and_table(self):
        pack = self.pack()
        self.assertEqual(pack.assign("giochi", "coin", ["sm64/sm64_bowser_laugh.wav"]), ["tmk/sm64/sm64_bowser_laugh.wav"])
        self.assertEqual(pack.path("coin", "giochi").name, "sm64_bowser_laugh.wav")  # la scelta vince sulla tabella
        self.assertEqual(pack.path("coin", "voce").name, "sm64_yahoo.wav")           # lo stile voce ha la sua tabella
        pack.assign("voce", "risata", ["tmk/sm64/sm64_bowser_laugh.wav"])           # nome nuovo per le avventure
        self.assertIn("risata", pack.all_names())
        self.assertEqual(pack.path("risata", "giochi"), None)                        # solo nello stile voce
        self.assertEqual(pack.path("risata", "voce").name, "sm64_bowser_laugh.wav")
        with self.assertRaises(ValueError):
            pack.assign("giochi", "Coin!", [])
        with self.assertRaises(ValueError):
            pack.assign("giochi", "coin", ["tmk/manca.wav"])
        with self.assertRaises(ValueError):
            pack.assign("sintetici", "coin", [])
        self.assertEqual(pack.set_volume(400), 150)
        again = self.pack()                                                          # riletto dal file
        self.assertEqual((again.volume, again.assignments["voce"]["risata"]), (150, ["tmk/sm64/sm64_bowser_laugh.wav"]))
        row = next(r for r in again.table() if r["name"] == "coin")
        self.assertEqual([f["rel"] for f in row["giochi"]["default"]], ["tmk/smb/smb_coin.wav"])
        self.assertEqual([f["rel"] for f in row["giochi"]["override"]], ["tmk/sm64/sm64_bowser_laugh.wav"])
        self.assertIsNone(row["voce"]["override"])
        pack.assign("giochi", "coin", [])                                             # ripristina
        self.assertEqual(self.pack().path("coin", "giochi").name, "smb_coin.wav")

    def test_custom_upload_delete_and_catalog(self):
        pack = self.pack()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(b"\x01\x00" * 2205)
        item = pack.add_custom(buf.getvalue(), "Risata di Leo!.wav", "risata")
        self.assertTrue(item["rel"].startswith("custom/Risata-di-Leo-"))
        self.assertEqual((item["section"], item["description"]), ("custom", "risata"))
        pack.assign("giochi", "coin", [item["rel"]])
        cat = pack.catalog(section="custom")
        self.assertEqual((cat["matches"], cat["sections"]["custom"]), (1, 1))
        self.assertTrue(cat["items"][0]["browser"])
        self.assertEqual(pack.catalog(query="overworld")["items"][0]["section"], "mid")
        self.assertEqual(pack.catalog(section="wav", game="smb")["matches"], 2)
        with self.assertRaises(ValueError):
            pack.delete_custom("tmk/smb/smb_coin.wav")
        self.assertTrue(pack.delete_custom(item["rel"]))
        self.assertNotIn("coin", pack.assignments["giochi"])  # tolto anche dalle scelte
        self.assertEqual(pack.catalog(section="custom")["matches"], 0)

    def test_playable_conversions(self):
        pack = self.pack()
        self.assertEqual(pack.playable("tmk/smb/smb_coin.wav").name, "smb_coin.wav")
        import shutil as _sh
        if _sh.which("ffmpeg"):
            cut = pack.playable("tmk/smb/smb_coin.wav", seconds=0.05)  # taglio: passa da ffmpeg e finisce in cache
            self.assertEqual(cut.parent, self.root / "cache")
            self.assertIs(pack.playable("tmk/smb/smb_coin.wav", seconds=0.05).samefile(cut), True)
        self.assertIsNone(pack.playable("tmk/manca.wav"))
        midi = pack.can_play("tmk/mid/smb/smb_over.mid", (True, None))
        self.assertFalse(midi)  # senza fluidsynth/timidity il MIDI non si suona
        self.assertTrue(pack.can_play("tmk/mid/smb/smb_over.mid", (False, "timidity")))

    def test_speaker_uses_pack_then_synth(self):
        sp = Speaker(enabled=False, engine=None, soundpack=self.pack())
        self.assertEqual(sp.sound_style, "giochi")
        self.assertEqual(sp.sound_path("coin").name, "smb_coin.wav")
        self.assertEqual(sp.sound_path("think").name, "think.wav")  # non scaricato: sintetizzato
        sp.configure(sound_style="voce")
        self.assertEqual(sp.sound_path("coin").name, "sm64_yahoo.wav")
        sp.configure(sound_style="boh")  # valore non valido: ignorato
        self.assertEqual(sp.status()["sound_style"], "voce")
        sp.configure(sound_style="sintetici")
        self.assertEqual(sp.sound_path("coin").name, "coin.wav")
        self.assertEqual(sp.status()["soundpack"]["files"], 5)
        self.assertIsNone(sp.sound_path("inesistente"))
        sp.player = ["paplay"]
        sp.pack.set_volume(50)
        self.assertEqual(sp.effect_command(Path("/x.wav")), ["paplay", "--volume=32768", "/x.wav"])
        sp.player = ["pw-play"]
        self.assertEqual(sp.effect_command(Path("/x.wav"))[1], "--volume=0.50")

    def test_every_rule_points_to_a_downloaded_game(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("fetch", PROJECT / "tools" / "fetch_mario_sounds.py")
        fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetch)
        games = {g for table in (self.sp.EFFECTS, self.sp.VOICE) for rules in table.values() for g, _d in rules}
        self.assertLessEqual(games, set(fetch.DEFAULT_GAMES))  # i giochi scaricati di default bastano
        page = ('<h4 class="subhead">Mario</h4><table><tr><td><a href="/sounds/wav/sm64/sm64_yahoo.wav" '
                'title="Download">&quot;Yahoo!&quot;</a></td><td class="sizebytes">30KB</td></tr></table>')
        self.assertEqual(fetch.parse_page("sm64", page), [{
            "section": "wav", "game": "sm64", "category": "Mario", "description": '"Yahoo!"',
            "url": "https://themushroomkingdom.net/sounds/wav/sm64/sm64_yahoo.wav", "file": "sm64/sm64_yahoo.wav",
            "size": 30720}])
        mid = fetch.parse_mid_page("smb", '<h2>Super Mario Bros. (NES)</h2><tr><td><a href="sounds/mid/smb_over.mid">'
                                          'Overworld</a></td><td class="num">11KB</td><td>PJ Barnes</td></tr>')
        self.assertEqual((mid[0]["file"], mid[0]["category"], mid[0]["extra"]),
                         ("mid/smb/smb_over.mid", "Super Mario Bros. (NES)", "MIDI di PJ Barnes"))
        zips = fetch.parse_zip_page("nsf", '<a href="stuff/in_nsf.zip">plugin</a> <a href="sounds/nsf/nsf_smb.zip">'
                                           'Super Mario Bros.</a> - 19 total tracks')
        self.assertEqual([(z["file"], z["extra"]) for z in zips], [("nsf/nsf_smb.zip", "19 total tracks")])
        self.assertEqual(fetch.game_pages('<a href="media/sm64/mid">SM64</a><a href="media/sm64/mid">Super Mario 64</a>',
                                          "mid"), [("sm64", "Super Mario 64")])


if __name__ == "__main__":
    unittest.main()
