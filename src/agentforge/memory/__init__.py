"""Memory package.

Phase 3 adds evidence-first evolution, verification levels, provenance,
embedding-based retrieval and feedback. The original MemoryStore interface is
preserved for agents.
"""

from agentforge.memory.base import MemoryEntry, MemoryStore, tokenize
from agentforge.memory.embeddings import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    cosine_similarity,
)
from agentforge.memory.engine import MemoryEvolutionEngine
from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.memory.in_memory import InMemoryMemoryStore
from agentforge.memory.models import (
    EvolvedMemory,
    MemoryCandidate,
    MemoryEvolutionResult,
    MemoryFeedback,
    MemoryRetrievalConfig,
    MemorySource,
    MemoryStats,
    MemoryType,
    PromotionState,
    VerificationLevel,
)
from agentforge.memory.sqlite_store import SQLiteMemoryStore

__all__ = [
    "EmbeddingProvider",
    "EvolvedMemory",
    "EvolutionMemoryStore",
    "HashingEmbeddingProvider",
    "InMemoryMemoryStore",
    "MemoryCandidate",
    "MemoryEntry",
    "MemoryEvolutionEngine",
    "MemoryEvolutionResult",
    "MemoryFeedback",
    "MemoryRetrievalConfig",
    "MemorySource",
    "MemoryStats",
    "MemoryStore",
    "MemoryType",
    "PromotionState",
    "SQLiteMemoryStore",
    "VerificationLevel",
    "cosine_similarity",
    "tokenize",
]
