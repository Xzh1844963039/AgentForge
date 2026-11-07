"""Conversation lifecycle management."""

from __future__ import annotations

from agentforge.models.common import HUMAN_SENDER
from agentforge.models.message import Conversation, ConversationType
from agentforge.persistence.database import SQLiteStore


class ConversationManager:
    """Create and retrieve direct and group conversations."""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def get(self, conversation_id: str) -> Conversation | None:
        return self._store.get_conversation(conversation_id)

    def require(self, conversation_id: str) -> Conversation:
        conversation = self.get(conversation_id)
        if conversation is None:
            raise KeyError(f"unknown conversation {conversation_id!r}")
        return conversation

    def list(self, limit: int = 50) -> list[Conversation]:
        return self._store.list_conversations(limit)

    def get_or_create_direct(
        self, first: str, second: str, *, title: str | None = None
    ) -> Conversation:
        if first == second:
            raise ValueError("a direct conversation needs two distinct participants")
        conversation_id = _direct_conversation_id(first, second)
        existing = self.get(conversation_id)
        if existing is not None:
            return existing

        participants = [first, second]
        human_first = HUMAN_SENDER in participants
        conversation = Conversation(
            conversation_id=conversation_id,
            conversation_type=ConversationType.DIRECT,
            title=title or "Direct: " + " <-> ".join(participants),
            participants=participants,
            metadata={"human_first": human_first},
        )
        self._store.save_conversation(conversation)
        return conversation

    def create_group(
        self,
        title: str,
        agent_ids: list[str],
        *,
        human_id: str = HUMAN_SENDER,
        metadata: dict | None = None,
    ) -> Conversation:
        participants = [human_id, *agent_ids]
        if len(set(participants)) != len(participants):
            raise ValueError("group participants must be unique")
        conversation = Conversation(
            conversation_type=ConversationType.GROUP,
            title=title,
            participants=participants,
            metadata=metadata or {},
        )
        self._store.save_conversation(conversation)
        return conversation


def _direct_conversation_id(first: str, second: str) -> str:
    ordered = sorted([first, second])
    safe = [value.replace(" ", "_") for value in ordered]
    return f"dm_{safe[0]}__{safe[1]}"
