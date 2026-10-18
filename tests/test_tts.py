"""Voce dal PC: riga di comando dei motori, limiti, effetti sonori sintetizzati (nessun processo avviato)."""
import unittest
import wave

from mario.assistant.tts import SOUND_RECIPES, Speaker, ensure_sound


class SpeakerCommandTests(unittest.TestCase):
    def test_spd_say_command_with_emotion(self):
        sp = Speaker(enabled=True, engine="spd-say", voice="Italian+Alex", rate=10, pitch=-10, volume=5)
        cmd = sp._command("Ciao a tutti", "gioia")  # gioia: +15 velocità, +20 tono
        self.assertEqual(cmd, ["spd-say", "-w", "-l", "it", "-r", "25", "-p", "10", "-i", "5", "-y", "Italian+Alex", "--", "Ciao a tutti"])
        self.assertEqual(sp._command("x", None)[4:8], ["-r", "10", "-p", "-10"])
        sp.configure(rate=95, pitch=-95)
        self.assertEqual(sp._command("x", "allarme")[4:8], ["-r", "100", "-p", "-65"])   # bloccati a +-100
        self.assertEqual(sp._command("x", "sonno")[4:8], ["-r", "55", "-p", "-100"])
        self.assertNotIn("-y", sp._command("x", None)) if not sp.voice else None

    def test_espeak_command_mapping(self):
        sp = Speaker(enabled=True, engine="espeak-ng", rate=0, pitch=0, volume=0)
        cmd = sp._command("Prova", "tristezza")  # tristezza: -25 velocità, -30 tono
        self.assertEqual(cmd[:2], ["espeak-ng", "-v"])
        self.assertEqual(cmd[cmd.index("-s") + 1], str(int(175 - 25 * 1.2)))
        self.assertEqual(cmd[cmd.index("-p") + 1], str(50 - 15))
        self.assertEqual(cmd[cmd.index("-a") + 1], "100")
        self.assertEqual(cmd[-1], "Prova")
        sp.configure(rate=100, pitch=100, volume=100)
        cmd = sp._command("x", "allarme")
        self.assertEqual((cmd[cmd.index("-s") + 1], cmd[cmd.index("-p") + 1], cmd[cmd.index("-a") + 1]), ("295", "99", "200"))

    def test_mario_effect_on_command_engines_and_wav(self):
        import shutil
        import tempfile
        import wave
        from pathlib import Path
        sp = Speaker(enabled=True, engine="spd-say", rate=0, pitch=0, voice_effect="mario")
        cmd = sp._command("Ciao", None)
        self.assertEqual(cmd[4:8], ["-r", "15", "-p", "50"])  # spd-say: più veloce e più acuta
        self.assertEqual(sp.status()["voice_effect"], "mario")
        sp.configure(voice_effect="boh")                      # valore non valido: ignorato
        self.assertEqual(sp.voice_effect, "mario")
        if not shutil.which("ffmpeg"):
            return
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frase.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
                w.writeframes(b"".join(int(8000 * __import__("math").sin(i / 10)).to_bytes(2, "little", signed=True)
                                       for i in range(22050)))
            self.assertTrue(sp._apply_effect(str(path)))
            with wave.open(str(path), "rb") as w:
                seconds = w.getnframes() / w.getframerate()
            self.assertAlmostEqual(seconds, 1 / 1.1, delta=0.08)  # velocità ×1.1
        sp.configure(voice_effect="")
        self.assertFalse(sp._apply_effect("/non/esiste.wav"))

    def test_configure_clamps_and_status(self):
        sp = Speaker(enabled=False, engine=None)
        sp.engine = None  # sull'host la rilevazione troverebbe spd-say
        sp.configure(rate=250, pitch=-250, volume="40", voice="X", enabled=True, sounds=False)
        self.assertEqual((sp.rate, sp.pitch, sp.volume, sp.voice, sp.sounds), (100, -100, 40, "X", False))
        st = sp.status()
        self.assertEqual((st["engine"], st["enabled"], st["queued"], st["voice"]), (None, True, 0, "X"))
        self.assertFalse(sp.available)
        sp.speak("non parte: nessun motore", "gioia")
        self.assertEqual(sp.spoken, [("non parte: nessun motore", "gioia")])
        self.assertTrue(sp.idle)
        self.assertFalse(sp.play("coin"))  # senza lettore o disabilitato: False, nessun errore

    def test_background_music_loops_until_stopped(self):
        import tempfile
        import time
        from pathlib import Path
        sp = Speaker(enabled=True, engine=None, sound_style="sintetici")
        sp.player = ["paplay"]
        self.assertEqual(sp.music_command(Path("x.wav"), 25), ["paplay", "--volume=16384", "x.wav"])
        sp.player = ["pw-play"]
        self.assertEqual(sp.music_command(Path("x.wav"), 250)[1], "--volume=1.00")
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "giri"
            sp.player = ["sh", "-c", 'echo "$1" >> "$0"; sleep 0.2', str(log)]   # lettore finto: un giro = 0,2 s
            self.assertFalse(sp.music("tmk/non/esiste.nsf"))
            self.assertFalse(sp.music("effetto-inesistente"))
            self.assertTrue(sp.music("coin", 20))
            self.assertEqual(sp.status()["music"], "coin")
            deadline = time.time() + 5
            while time.time() < deadline and (not log.exists() or len(log.read_text().splitlines()) < 2):
                time.sleep(0.05)
            self.assertGreaterEqual(len(log.read_text().splitlines()), 2)   # ricomincia da capo
            self.assertTrue(log.read_text().splitlines()[0].endswith("coin.wav"))
            sp.music_stop()
            self.assertIsNone(sp.music_playing)
            count = len(log.read_text().splitlines())
            time.sleep(0.7)
            self.assertEqual(len(log.read_text().splitlines()), count)       # fermata davvero
            sp.configure(sounds=False)
            self.assertFalse(sp.music("coin"))

    def test_sounds_are_valid_wav(self):
        for name in SOUND_RECIPES:
            path = ensure_sound(name)
            self.assertTrue(path.exists(), name)
            with wave.open(str(path), "rb") as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 22050))
                self.assertGreater(w.getnframes(), 500)
        self.assertIsNone(ensure_sound("inesistente"))


if __name__ == "__main__":
    unittest.main()
