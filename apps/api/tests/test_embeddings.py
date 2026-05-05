"""Tests for the embeddings abstraction. Network calls are stubbed."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class GetEmbeddingProviderTest(unittest.TestCase):
    """The factory should pick the right backend and memoise the result."""

    def setUp(self) -> None:
        import embeddings as _emb

        _emb._provider = None
        self.addCleanup(lambda: setattr(_emb, "_provider", None))

    def test_default_is_openai(self) -> None:
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "test-key"},
            clear=False,
        ):
            os.environ.pop("EMBEDDING_PROVIDER", None)
            from embeddings import OpenAIEmbeddingProvider, get_embedding_provider

            provider = get_embedding_provider()
            self.assertIsInstance(provider, OpenAIEmbeddingProvider)

    def test_explicit_vertex_returns_stub(self) -> None:
        with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "vertex"}):
            from embeddings import VertexAIEmbeddingProvider, get_embedding_provider

            provider = get_embedding_provider()
            self.assertIsInstance(provider, VertexAIEmbeddingProvider)
            self.assertEqual(provider.dim, 768)

    def test_provider_is_memoised(self) -> None:
        with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "vertex"}):
            from embeddings import get_embedding_provider

            self.assertIs(get_embedding_provider(), get_embedding_provider())


class OpenAIEmbeddingProviderTest(unittest.TestCase):
    def test_init_requires_api_key(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            from embeddings import OpenAIEmbeddingProvider

            with self.assertRaises(RuntimeError):
                OpenAIEmbeddingProvider()

    def test_embed_uses_client(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "k"}):
            from embeddings import OpenAIEmbeddingProvider

            provider = OpenAIEmbeddingProvider(model="m")

        # Replace the client with a stub.
        class _Resp:
            data = [type("D", (), {"embedding": [0.1, 0.2, 0.3]})()]

        called: dict = {}

        class _Embeds:
            def create(self, **kwargs):
                called.update(kwargs)
                return _Resp()

        class _Client:
            embeddings = _Embeds()

        provider._client = _Client()
        out = provider.embed(" hello ")
        self.assertEqual(out, [0.1, 0.2, 0.3])
        self.assertEqual(called["input"], "hello")

    def test_embed_batch_chunks(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "k"}):
            from embeddings import OpenAIEmbeddingProvider

            provider = OpenAIEmbeddingProvider()

        # Stub client returns one embedding per input it sees.
        chunks_seen: list[int] = []

        class _D:
            def __init__(self, n: int) -> None:
                self.embedding = [float(n)] * 3

        class _Resp:
            def __init__(self, n: int) -> None:
                self.data = [_D(i) for i in range(n)]

        class _Embeds:
            def create(self, **kwargs):
                inputs = kwargs["input"]
                chunks_seen.append(len(inputs))
                return _Resp(len(inputs))

        class _Client:
            embeddings = _Embeds()

        provider._client = _Client()
        # 600 inputs → CHUNK=256 → 256, 256, 88
        out = provider.embed_batch([f"text {i}" for i in range(600)])
        self.assertEqual(len(out), 600)
        self.assertEqual(chunks_seen, [256, 256, 88])


class VertexAIEmbeddingProviderTest(unittest.TestCase):
    def test_embed_raises_not_implemented(self) -> None:
        from embeddings import VertexAIEmbeddingProvider

        with self.assertRaises(NotImplementedError):
            VertexAIEmbeddingProvider().embed("x")

    def test_embed_batch_raises_not_implemented(self) -> None:
        from embeddings import VertexAIEmbeddingProvider

        with self.assertRaises(NotImplementedError):
            VertexAIEmbeddingProvider().embed_batch(["x"])


if __name__ == "__main__":
    unittest.main()
