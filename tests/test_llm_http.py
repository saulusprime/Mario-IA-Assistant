"""Backend HTTP dell'assistente contro un server locale finto che parla SSE (senza rete esterna)."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class QuietServer(ThreadingHTTPServer):
    """Non stampa il traceback quando il client chiude a metà risposta (prova dello Stop)."""

    def handle_error(self, request, client_address):
        pass

from mario.assistant.llm import AnthropicBackend, LLMConfig, LLMError, OpenAICompatBackend, make_backend

RECEIVED: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silenzioso
        pass

    def _sse(self, lines):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for line in lines:
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        RECEIVED.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        model = body.get("model", "")
        if model == "err400":
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"type": "invalid_request_error", "message": "temperature is deprecated"}}).encode())
            return
        if model == "err401":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"no key")
            return
        if self.path.endswith("/messages"):  # Anthropic
            if model == "sse-error":
                self._sse(["event: error\n", 'data: {"type":"error","error":{"type":"overloaded_error","message":"riprova"}}\n\n'])
                return
            lines = ["event: message_start\n", 'data: {"type":"message_start"}\n\n',
                     'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"[gioia] Ciao"}}\n\n',
                     'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" a te!"}}\n\n']
            if model == "cut":
                self._sse(lines[:3])
                self.close_connection = True
                return
            self._sse(lines + ['data: {"type":"message_stop"}\n\n', 'data: {"type":"ignorato"}\n\n'])
            return
        # OpenAI-compatibile
        if model == "sse-error":
            self._sse(['data: {"error":{"message":"modello non trovato"}}\n\n'])
            return
        self._sse(['data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
                   'data: {"choices":[{"delta":{"content":"[calma] Pronto"}}]}\n\n',
                   'data: {"choices":[{"delta":{"content":"."}}]}\n\n', "data: [DONE]\n\n",
                   'data: {"choices":[{"delta":{"content":"dopo DONE"}}]}\n\n'])


class HttpBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = QuietServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RECEIVED.clear()
        import mario.assistant.llm as llm
        self._url = llm.ANTHROPIC_URL
        llm.ANTHROPIC_URL = self.base + "/v1/messages"

    def tearDown(self):
        import mario.assistant.llm as llm
        llm.ANTHROPIC_URL = self._url

    def test_anthropic_stream_headers_and_body(self):
        backend = AnthropicBackend(LLMConfig(api_key="sk-test", model="ok", max_tokens=99, temperature=0.3))
        text = "".join(backend.stream("sistema", [{"role": "user", "content": "ciao", "extra": 1}]))
        self.assertEqual(text, "[gioia] Ciao a te!")
        req = RECEIVED[-1]
        self.assertEqual(req["headers"]["x-api-key"], "sk-test")
        self.assertEqual(req["headers"]["anthropic-version"], "2023-06-01")
        self.assertNotIn("temperature", req["body"])
        self.assertEqual((req["body"]["max_tokens"], req["body"]["system"], req["body"]["stream"]), (99, "sistema", True))
        self.assertEqual(req["body"]["messages"], [{"role": "user", "content": "ciao"}])
        self.assertEqual(backend.describe(), "anthropic (ok)")

    def test_openai_stream_and_done(self):
        backend = OpenAICompatBackend(LLMConfig(backend="openai", base_url=self.base + "/v1/", model="ok", api_key="k"))
        text = "".join(backend.stream("sistema", [{"role": "user", "content": "ciao"}]))
        self.assertEqual(text, "[calma] Pronto.")
        req = RECEIVED[-1]
        self.assertEqual(req["path"], "/v1/chat/completions")
        self.assertEqual(req["headers"]["authorization"], "Bearer k")
        self.assertEqual(req["body"]["temperature"], 0.8)
        self.assertEqual(req["body"]["messages"][0], {"role": "system", "content": "sistema"})
        self.assertIsInstance(make_backend(LLMConfig(backend="openai", base_url=self.base, model="x")), OpenAICompatBackend)

    def test_http_errors_become_llm_errors(self):
        with self.assertRaises(LLMError) as ctx:
            list(AnthropicBackend(LLMConfig(api_key="k", model="err400")).stream("s", []))
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertIn("temperature is deprecated", str(ctx.exception))
        with self.assertRaises(LLMError) as ctx:
            list(AnthropicBackend(LLMConfig(api_key="k", model="err401")).stream("s", []))
        self.assertIn("HTTP 401", str(ctx.exception))
        with self.assertRaises(LLMError) as ctx:
            list(AnthropicBackend(LLMConfig(api_key="k", model="sse-error")).stream("s", []))
        self.assertIn("overloaded_error", str(ctx.exception))
        with self.assertRaises(LLMError) as ctx:
            list(OpenAICompatBackend(LLMConfig(base_url=self.base, model="sse-error")).stream("s", []))
        self.assertIn("modello non trovato", str(ctx.exception))
        with self.assertRaises(LLMError) as ctx:
            list(OpenAICompatBackend(LLMConfig(base_url="http://127.0.0.1:1", model="x", timeout=2)).stream("s", []))
        self.assertIn("rete", str(ctx.exception).lower())

    def test_cut_connection_and_stop(self):
        backend = AnthropicBackend(LLMConfig(api_key="k", model="cut"))
        chunks = []
        try:
            for chunk in backend.stream("s", []):
                chunks.append(chunk)
        except LLMError as exc:
            self.assertIn("interrotta", str(exc))
        self.assertEqual(chunks, ["[gioia] Ciao"])  # quello che è arrivato prima del taglio resta utilizzabile
        gen = AnthropicBackend(LLMConfig(api_key="k", model="ok")).stream("s", [])
        self.assertEqual(next(gen), "[gioia] Ciao")
        gen.close()  # Stop: il generatore si chiude senza eccezioni e senza leggere il resto
        with self.assertRaises(StopIteration):
            next(gen)


if __name__ == "__main__":
    unittest.main()
