"""Local sentence-transformers embedding provider.

The model is loaded lazily so demo mode and unit tests never import torch or
download weights. Query and document text use different BGE instructions, but
both vectors come from the same model instance and profile.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from threading import Lock
from typing import Any


class LocalEmbedding:
    def __init__(
        self,
        *,
        model_name: str,
        dimensions: int,
        query_prefix: str = "",
        device: str = "cpu",
        batch_size: int = 16,
        local_files_only: bool = False,
        max_seq_length: int | None = None,
    ) -> None:
        model_name = model_name.strip()
        if not model_name or model_name == "fake":
            raise ValueError("a real local embedding model name is required")
        if dimensions < 1:
            raise ValueError("embedding dimensions must be positive")
        if batch_size < 1:
            raise ValueError("embedding batch size must be positive")
        if max_seq_length is not None and max_seq_length < 1:
            raise ValueError("max_seq_length must be positive")
        self.model_name = model_name
        self.dimensions = dimensions
        self.query_prefix = query_prefix
        self.device = device
        self.batch_size = batch_size
        self.local_files_only = local_files_only
        self.max_seq_length = max_seq_length
        self._model: Any | None = None
        self._load_lock = Lock()
        self._encode_lock = Lock()

    def _load_model(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # Keep demo and test installs lightweight.
                raise RuntimeError(
                    "sentence-transformers is not installed; install "
                    "apps/api[local-embeddings] before using a local embedding model"
                ) from exc
            try:
                model = SentenceTransformer(
                    self.model_name,
                    device=self.device,
                    local_files_only=self.local_files_only,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"unable to load local embedding model {self.model_name!r}"
                ) from exc
            if self.max_seq_length is not None:
                model.max_seq_length = self.max_seq_length
            self._model = model
            return model

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) for text in texts):
            raise TypeError("embedding input must contain only strings")
        with self._encode_lock:
            raw = self._load_model().encode(
                list(texts),
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        if len(raw) != len(texts):
            raise ValueError("embedding provider returned the wrong number of vectors")
        return [self._validate(vector) for vector in raw]

    def _validate(self, vector: Sequence[float]) -> list[float]:
        values = [float(value) for value in vector]
        if len(values) != self.dimensions:
            raise ValueError(
                f"embedding dimension mismatch: expected {self.dimensions}, got {len(values)}"
            )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding contains a non-finite value")
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            raise ValueError("embedding vector must not be zero")
        return [value / norm for value in values]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_document(self, text: str) -> list[float]:
        return self._encode([text])[0]

    def embed_query(self, text: str) -> list[float]:
        return self._encode([self.query_prefix + text])[0]