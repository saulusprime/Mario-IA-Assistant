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
