"""
Embedding provider abstraction.

Default local: OpenAI `text-embedding-3-small` (1536-dim) via the direct OpenAI API.
   - Works on every platform (no native deps like torch/onnxruntime).
   - ~$0.02 / 1M tokens — negligible at demo scale.
   - Reuses the OPENAI_API_KEY needed for Whisper/TTS in Phase 2.

Optional: sentence-transformers/all-MiniLM-L6-v2 (384-dim). In-process, free,
offline — but requires torch which doesn't ship wheels for some platforms
(e.g. macOS x86_64 on macOS 26+). Enable via EMBEDDING_PROVIDER=local_st.

Prod (GCP): Vertex AI text-embedding-005 (stub — implement on migration).

Swap via env var: EMBEDDING_PROVIDER=openai|local_st|vertex. Default: openai.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Protocol, Sequence

log = logging.getLogger("medai.embeddings")


class EmbeddingProvider(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbeddingProvider:
    """OpenAI text-embedding-3-small via the direct API.

    Cheap, reliable, no platform constraints. Dimensions = 1536.
    """

    dim = 1536

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        from openai import OpenAI

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is required for OpenAIEmbeddingProvider. "
                "Add it to apps/api/.env.local or set EMBEDDING_PROVIDER=local_st "
                "to use sentence-transformers instead."
            )
        self.model = model
        self._client = OpenAI(api_key=key)

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(
            model=self.model,
            input=(text or "").strip() or " ",
        )
        return list(response.data[0].embedding)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        # OpenAI accepts up to ~2048 inputs per request; we'll chunk defensively.
        cleaned = [(t or "").strip() or " " for t in texts]
        out: list[list[float]] = []
        CHUNK = 256
        for i in range(0, len(cleaned), CHUNK):
            batch = cleaned[i : i + CHUNK]
            response = self._client.embeddings.create(model=self.model, input=batch)
            out.extend(list(d.embedding) for d in response.data)
        return out


class SentenceTransformerProvider:
    """Local, CPU-capable embeddings via sentence-transformers. Requires torch.

    Model is lazy-loaded on first use; weights (~90 MB) cached in-process.
    """

    dim = 384

    _model = None
    _lock = threading.Lock()

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_name = model_name

    def _ensure_model(self):
        if SentenceTransformerProvider._model is None:
            with SentenceTransformerProvider._lock:
                if SentenceTransformerProvider._model is None:
                    from sentence_transformers import SentenceTransformer  # type: ignore

                    log.info("loading sentence-transformers model: %s", self.model_name)
                    SentenceTransformerProvider._model = SentenceTransformer(self.model_name)
        return SentenceTransformerProvider._model

    def embed(self, text: str) -> list[float]:
        model = self._ensure_model()
        vec = model.encode(text or "", normalize_embeddings=True)
        return [float(x) for x in vec.tolist()]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._ensure_model()
        vecs = model.encode(list(texts), normalize_embeddings=True, batch_size=16)
        return [[float(x) for x in v.tolist()] for v in vecs]


class VertexAIEmbeddingProvider:
    """Stub — implement when migrating to GCP.

    Plan: Vertex AI `text-embedding-005` (768-dim). Dims differ from the other
    providers, so stored embeddings need a one-shot re-embed at migration time.
    """

    dim = 768

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError(
            "VertexAIEmbeddingProvider not yet implemented. "
            "Implement on GCP migration using google-cloud-aiplatform."
        )

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError("VertexAIEmbeddingProvider not yet implemented.")


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    """Return the configured embedding provider. Safe to call repeatedly."""
    global _provider
    if _provider is not None:
        return _provider

    backend = (os.environ.get("EMBEDDING_PROVIDER") or "openai").lower()
    if backend == "vertex":
        _provider = VertexAIEmbeddingProvider()
    elif backend in ("local_st", "sentence_transformers", "st"):
        _provider = SentenceTransformerProvider()
    else:
        _provider = OpenAIEmbeddingProvider()
    return _provider
