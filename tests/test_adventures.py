"""Designer di avventure: modello, validazione, motore con eventi simulati (nessun Bluetooth)."""
import json
import unittest

from mario.adventures import Adventure, AdventureRunner, Trigger, load_examples, validate
from mario.adventures.library import describe_trigger, library


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FakeSpeaker:
    def __init__(self):
        self.spoken, self.played = [], []

    def speak(self, text, emotion=None):
        self.spoken.append((text, emotion))

    def play(self, name):
        self.played.append(name)


def gesture(bits):
    return {"type": "PORT_VALUE_SINGLE", "io_type": 0x47, "mode": 1, "data": bits.to_bytes(2, "little")}


def scanner(barcode=0xFFFF, color=0xFFFF):
    return {"type": "PORT_VALUE_SINGLE", "io_type": 0x49, "mode": 0, "data": barcode.to_bytes(2, "little") + color.to_bytes(2, "little")}


def pants(code):
    return {"type": "PORT_VALUE_SINGLE", "io_type": 0x4A, "mode": 0, "data": bytes([code])}


def game_event(code, value):
    return {"type": "PORT_VALUE_SINGLE", "io_type": 0x46, "mode": 2, "data": code.to_bytes(2, "little") + value.to_bytes(2, "little")}


BUTTON = {"type": "HUB_PROPERTIES", "prop": "BUTTON", "value": True}


def make(steps, **kw):
    return Adventure.from_dict({"name": "prova", "steps": steps, **kw})


class ModelTests(unittest.TestCase):
    def test_examples_are_valid_and_round_trip(self):
        examples = load_examples()
        self.assertEqual(len(examples), 3)
        for adv in examples:
            self.assertEqual(validate(adv), [], adv.name)
            again = Adventure.from_json(adv.to_json())
            self.assertEqual(again.to_dict(), adv.to_dict())
        self.assertEqual(describe_trigger(Trigger.from_dict({"kind": "barcode", "code": "0x0002"})), "basetta Goomba")
        self.assertEqual(describe_trigger(Trigger.from_dict({"kind": "count", "of": {"kind": "gesture", "name": "salto"}, "times": 3})),
                         "gesto salto × 3")
        lib = library({0x0002})
        self.assertTrue(any(t["owned"] and t["name"] == "Goomba" for t in lib["tiles"]))
        self.assertEqual(lib["gestures"][0]["name"], "salto")

    def test_validation_errors(self):
        adv = make([{"id": "a", "trigger": {"kind": "boh"}, "next": "zz"},
                    {"id": "a", "trigger": {"kind": "gesture", "name": "piroetta"}, "on_success": [{"kind": "volume", "value": 500}, {"kind": "goto", "value": "nope"}]},
                    {"id": "c", "trigger": {"kind": "sequence", "steps": [{"kind": "button"}]}},
                    {"id": "d", "trigger": {"kind": "count", "of": {"kind": "color"}, "times": 1}, "timeout": -1}])
        problems = validate(adv)
        for needle in ("duplicati", "sconosciuto 'boh'", "inesistente 'zz'", "piroetta", "volume fuori", "goto verso",
                       "meno di due", "count richiede", "timeout negativo"):
            self.assertTrue(any(needle in p for p in problems), (needle, problems))
        self.assertIn("nessun passo", validate(Adventure.from_dict({"name": " "})))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.events = []
        self.speaker = FakeSpeaker()
        self.volumes = []

    def runner(self, adv):
        return AdventureRunner(adv, emit=self.events.append, speaker=self.speaker, volume_setter=self.volumes.append,
                               clock=self.clock)

    def kinds(self):
        return [e["kind"] for e in self.events]

    def test_simple_triggers_score_and_finish(self):
        adv = make([
            {"id": "verde", "text": "trova il verde", "trigger": {"kind": "color", "code": "0x0025"}, "points": 10, "success_text": "erba!"},
            {"id": "goomba", "trigger": {"kind": "barcode", "family": "enemy"}, "points": 5},
            {"id": "fuoco", "trigger": {"kind": "pants", "code": "0x12"}, "points": 5, "on_success": [{"kind": "volume", "value": 80}, {"kind": "score", "value": 3}]},
            {"id": "monete", "trigger": {"kind": "game_event", "code": "0x386B", "min_value": 5}, "points": 7},
            {"id": "tasto", "trigger": {"kind": "button"}, "points": 1},
        ], intro_text="via!", end_text="fine con {score}")
        r = self.runner(adv)
        r.start()
        self.assertEqual(r.state, "running")
        self.assertEqual(self.speaker.spoken[0], ("via!", "gioia"))
        self.assertEqual(self.speaker.spoken[1][0], "trova il verde")
        self.assertFalse(r.handle_event(scanner(color=0x0015)))      # rosso: no
        self.assertTrue(r.handle_event(scanner(color=0x0025)))       # verde: sì
        self.assertEqual((r.score, r.index), (10, 1))
        self.assertIn("coin", self.speaker.played)
        self.assertFalse(r.handle_event(scanner(barcode=0x00B8)))    # start non è un nemico
        self.assertTrue(r.handle_event(scanner(barcode=0x0002)))     # Goomba
        self.assertEqual([p["name"] for p in r.path], ["Start Mario", "Goomba"])
        self.assertTrue(r.handle_event(pants(0x12)))
        self.assertEqual((r.score, self.volumes), (23, [80]))
        self.assertFalse(r.handle_event(game_event(0x386B, 4)))
        self.assertTrue(r.handle_event(game_event(0x386B, 6)))
        self.assertTrue(r.handle_event(BUTTON))
        self.assertEqual((r.state, r.outcome, r.score), ("finished", "completata", 31))
        self.assertEqual(self.events[-1]["text"], "fine con 31")
        self.assertEqual([x["outcome"] for x in r.step_results], ["ok"] * 5)
        self.assertFalse(r.handle_event(BUTTON))  # finita: niente più progressi
        st = r.status()
        self.assertEqual((st["state"], st["step"], st["context"]["pants"]), ("finished", None, "Fuoco"))

    def test_timeout_timer_and_time_limit(self):
        adv = make([
            {"id": "a", "trigger": {"kind": "gesture", "name": "salto"}, "timeout": 10, "timeout_text": "troppo lento", "on_timeout": [{"kind": "message", "text": "peccato"}]},
            {"id": "b", "trigger": {"kind": "timer", "seconds": 3}, "points": 2},
            {"id": "c", "trigger": {"kind": "gesture", "name": "scossa"}, "timeout": 0},
        ], time_limit=60)
        r = self.runner(adv)
        r.start()
        self.clock.advance(9)
        r.tick()
        self.assertEqual(r.index, 0)
        self.clock.advance(1.5)
        r.tick()
        self.assertEqual(r.index, 1)
        self.assertEqual(r.step_results[0]["outcome"], "tempo scaduto")
        self.assertIn(("troppo lento", "tristezza"), self.speaker.spoken)
        self.assertIn("message", self.kinds())
        self.clock.advance(2)
        r.tick()
        self.assertEqual(r.index, 1)
        self.clock.advance(1.5)
        r.tick()
        self.assertEqual((r.index, r.score), (2, 2))
        self.clock.advance(60)
        r.tick()
        self.assertEqual((r.state, r.outcome), ("finished", "tempo scaduto"))

    def test_count_and_sequence(self):
        adv = make([
            {"id": "tre", "trigger": {"kind": "count", "of": {"kind": "gesture", "name": "salto"}, "times": 3, "seconds": 1.0}, "points": 15},
            {"id": "combo", "trigger": {"kind": "sequence", "steps": [{"kind": "gesture", "name": "salto"}, {"kind": "gesture", "name": "scossa"}], "seconds": 5}, "points": 25},
        ])
        r = self.runner(adv)
        r.start()
        self.assertFalse(r.handle_event(gesture(0x8000)))
        self.assertFalse(r.handle_event(gesture(0x8000)))   # troppo ravvicinato: non conta
        self.assertEqual(r.status()["step"]["progress"], "1/3")
        self.clock.advance(1.1)
        self.assertFalse(r.handle_event(gesture(0x8000)))
        self.clock.advance(1.1)
        self.assertTrue(r.handle_event(gesture(0x8000)))
        self.assertEqual((r.index, r.score), (1, 15))
        # sequenza: salto, poi troppo tardi la scossa -> si riparte
        self.clock.advance(2)
        self.assertFalse(r.handle_event(gesture(0x8000)))
        self.assertEqual(r.status()["step"]["progress"], "1/2")
        self.clock.advance(6)
        self.assertFalse(r.handle_event(gesture(0x0010)))   # fuori tempo: progresso azzerato
        self.assertEqual(r.status()["step"]["progress"], "0/2")
        self.clock.advance(1)
        self.assertFalse(r.handle_event(gesture(0x8000)))
        self.clock.advance(1)
        self.assertTrue(r.handle_event(gesture(0x0010)))
        self.assertEqual((r.state, r.score), ("finished", 40))
        self.assertIn("progress", self.kinds())

    def test_goto_end_webhook_and_stop(self):
        adv = make([
            {"id": "a", "trigger": {"kind": "button"}, "points": 0, "on_success": [{"kind": "goto", "value": "c"}, {"kind": "webhook", "text": "http://127.0.0.1:9/x"}]},
            {"id": "b", "trigger": {"kind": "button"}, "points": 100},
            {"id": "c", "trigger": {"kind": "button"}, "points": 1, "on_success": [{"kind": "say", "text": "ciao", "value": "gioia"}, {"kind": "end", "value": "terminata"}]},
            {"id": "d", "trigger": {"kind": "button"}, "points": 100},
        ])
        r = self.runner(adv)
        r.start()
        r.handle_event(BUTTON)
        self.assertEqual(r.current_step.id, "c")
        self.assertTrue(any(e["kind"] == "webhook" and "non inviato" in e["text"] for e in self.events))
        r.handle_event(BUTTON)
        self.assertEqual((r.state, r.outcome, r.score), ("finished", "terminata", 1))
        self.assertIn(("ciao", "gioia"), self.speaker.spoken)
        r2 = self.runner(adv)
        r2.start()
        r2.stop()
        self.assertEqual((r2.state, r2.outcome), ("finished", "abbandonata"))
        self.assertEqual(json.loads(json.dumps(r2.status(), default=str))["outcome"], "abbandonata")

    def test_combined_event_and_lifted_context(self):
        adv = make([{"id": "a", "trigger": {"kind": "gesture", "name": "rotazione"}}])
        r = self.runner(adv)
        r.start()
        r.handle_event(scanner())
        self.assertTrue(r.context["lifted"])
        ev = {"type": "PORT_VALUE_COMBINED", "io_type": 0x47, "modes": {"0": {"data": "01 02 03"}, "1": {"data": "00 01 00 01"}}}
        self.assertTrue(r.handle_event(ev))
        self.assertEqual(r.state, "finished")


if __name__ == "__main__":
    unittest.main()
