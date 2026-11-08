"""Deterministic local embeddings for memory retrieval.

This is a lightweight, offline hashing embedding. It is an embedding interface
and a reproducible vector representation, not a learned semantic model. Real
embedding quality is NOT VERIFIED without an external embedding provider.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class EmbeddingProvider(ABC):
    name: str = "base"
    dimension: int

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return a fixed-size normalized vector."""


class HashingEmbeddingProvider(EmbeddingProvider):
    """Deterministic signed feature hashing over tokens and character trigrams."""

    name = "local-hashing"

    def __init__(self, dimension: int = 256) -> None:
        if dimension < 16:
            raise ValueError("embedding dimension must be >= 16")
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = [token.lower() for token in _TOKEN_RE.findall(text)]
        if not tokens:
            return vector

        for token in tokens:
            self._add(vector, token, 1.0)
            for index in range(max(1, len(token) - 2)):
                trigram = token[index : index + 3]
                if len(trigram) == 3:
                    self._add(vector, trigram, 0.35)

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    def _add(self, vector: list[float], feature: str, weight: float) -> None:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % self.dimension
        sign = 1.0 if (value >> 63) & 1 == 0 else -1.0
        vector[index] += sign * weight


def cosine_similarity(first: list[float], second: list[float]) -> float:
    if not first or not second or len(first) != len(second):
        return 0.0
    dot = sum(a * b for a, b in zip(first, second))
    first_norm = math.sqrt(sum(value * value for value in first))
    second_norm = math.sqrt(sum(value * value for value in second))
    if first_norm == 0 or second_norm == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (first_norm * second_norm)))
