"""OllamaEmbedder against a real HTTP server (not a hand-rolled fake) --
covers request shaping (num_ctx passthrough) and response parsing
(distinguishing a context-length rejection from any other server error),
both load-bearing for the split-retry and split-model-recommendation work.
"""
import json
import http.server
import sys
import os
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from vll_organism.embedder import EmbeddingError, EmbeddingInputTooLongError, OllamaEmbedder


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Echoes a canned response and records the last request body so tests
    can assert on exactly what OllamaEmbedder sent."""

    response_status = 200
    response_body = {"embeddings": [[0.1, 0.2, 0.3]]}
    last_request_body = None
    last_request_path = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        type(self).last_request_path = self.path
        type(self).last_request_body = json.loads(self.rfile.read(length))
        payload = json.dumps(type(self).response_body).encode()
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def mock_ollama():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    port = srv.server_port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _RecordingHandler.response_status = 200
    _RecordingHandler.response_body = {"embeddings": [[0.1, 0.2, 0.3]]}
    _RecordingHandler.last_request_body = None
    _RecordingHandler.last_request_path = None
    yield f"http://127.0.0.1:{port}", _RecordingHandler
    srv.shutdown()


def test_num_ctx_omitted_by_default(mock_ollama):
    base_url, handler = mock_ollama
    emb = OllamaEmbedder(model="qwen3-embedding:8b", base_url=base_url, dim=4096)
    emb.embed("hello")
    assert handler.last_request_path == "/api/embed"
    assert handler.last_request_body["input"] == "hello"
    assert handler.last_request_body["truncate"] is False
    assert "options" not in handler.last_request_body, (
        "no num_ctx configured -- the request must not send an options block at all, "
        "letting Ollama use its own default"
    )


def test_num_ctx_passed_through_when_set(mock_ollama):
    base_url, handler = mock_ollama
    emb = OllamaEmbedder(model="nomic-embed-text", base_url=base_url, dim=768, num_ctx=8192)
    emb.embed("hello")
    assert handler.last_request_body["options"] == {"num_ctx": 8192}


def test_context_length_error_raises_specific_subtype(mock_ollama):
    base_url, handler = mock_ollama
    handler.response_status = 500
    handler.response_body = {"error": "llm embedding error: the input length exceeds the context length"}
    emb = OllamaEmbedder(model="all-minilm", base_url=base_url, dim=384)
    with pytest.raises(EmbeddingInputTooLongError) as excinfo:
        emb.embed("x" * 5000)
    assert "context" in str(excinfo.value).lower()


def test_other_server_error_raises_generic_embedding_error_with_real_body(mock_ollama):
    base_url, handler = mock_ollama
    handler.response_status = 500
    handler.response_body = {"error": "model 'all-minilm' not found, try pulling it first"}
    emb = OllamaEmbedder(model="all-minilm", base_url=base_url, dim=384)
    with pytest.raises(EmbeddingError) as excinfo:
        emb.embed("hello")
    assert not isinstance(excinfo.value, EmbeddingInputTooLongError)
    assert "not found" in str(excinfo.value), (
        "the real server-reported reason must surface, not just a generic "
        "'is Ollama running?' guess that discards it"
    )
