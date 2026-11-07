"""Durable message bus with direct, group and agent-to-agent routing."""

from __future__ import annotations

from typing import Any, Protocol

from agentforge.messaging.conversation import ConversationManager
from agentforge.models.common import HUMAN_SENDER, SYSTEM_SENDER
from agentforge.models.message import (
    Conversation,
    ConversationType,
    Message,
    MessageType,
)
from agentforge.persistence.database import SQLiteStore
from agentforge.persistence.events import EventLogger


class AgentLike(Protocol):
    """Minimal agent contract required for message delivery."""

    agent_id: str

    def deliver(self, message: Message) -> None: ...


class MessageBus:
    """Routes messages and archives every message in SQLite.

    Routing rules:
    - direct: delivered to the receiver if it is a registered agent;
    - group: archived and delivered to every agent participant except the sender;
    - human senders/receivers have no in-process inbox.
    """

    def __init__(
        self,
        store: SQLiteStore,
        conversations: ConversationManager,
        events: EventLogger,
        *,
        human_id: str = HUMAN_SENDER,
    ) -> None:
        self._store = store
        self._conversations = conversations
        self._events = events
        self._human_id = human_id
        self._agents: dict[str, AgentLike] = {}

    def register_agent(self, agent: AgentLike) -> None:
        if agent.agent_id in self._agents:
            raise ValueError(f"agent {agent.agent_id!r} is already registered")
        self._agents[agent.agent_id] = agent

    def send(
        self,
        *,
        sender: str,
        conversation_id: str,
        message_type: MessageType,
        content: str,
        receiver: str | None = None,
        correlation_id: str | None = None,
        task_id: str | None = None,
        in_reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        deliver: bool = True,
    ) -> Message:
        conversation = self._conversations.require(conversation_id)
        receiver = self._resolve_receiver(conversation, sender, receiver)
        self._validate_sender(conversation, sender)
        self._validate_receiver(conversation, receiver)

        message = Message(
            conversation_id=conversation_id,
            sender=sender,
            receiver=receiver,
            message_type=message_type,
            content=content,
            correlation_id=correlation_id,
            task_id=task_id,
            in_reply_to=in_reply_to,
            metadata=metadata or {},
        )
        self._store.save_message(message)
        self._events.event(
            "message_sent",
            message_id=message.message_id,
            conversation_id=conversation_id,
            sender=sender,
            receiver=receiver,
            message_type=message_type.value,
            task_id=task_id,
            correlation_id=correlation_id,
            content_preview=content[:160],
        )
        if deliver:
            self._deliver(conversation, message)
        return message

    def send_direct(
        self,
        *,
        sender: str,
        receiver: str,
        message_type: MessageType,
        content: str,
        correlation_id: str | None = None,
        task_id: str | None = None,
        in_reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        deliver: bool = True,
    ) -> Message:
        conversation = self._conversations.get_or_create_direct(sender, receiver)
        return self.send(
            sender=sender,
            conversation_id=conversation.conversation_id,
            message_type=message_type,
            content=content,
            receiver=receiver,
            correlation_id=correlation_id,
            task_id=task_id,
            in_reply_to=in_reply_to,
            metadata=metadata,
            deliver=deliver,
        )

    def replay(self, conversation_id: str, *, limit: int | None = None) -> list[Message]:
        return self._store.list_messages(conversation_id, limit=limit)

    def agents_in(self, conversation_id: str) -> list[str]:
        conversation = self._conversations.require(conversation_id)
        return [
            participant
            for participant in conversation.participants
            if participant in self._agents
        ]

    def _resolve_receiver(
        self, conversation: Conversation, sender: str, receiver: str | None
    ) -> str:
        if conversation.conversation_type == ConversationType.GROUP:
            return receiver or conversation.conversation_id

        others = [
            participant
            for participant in conversation.participants
            if participant != sender
        ]
        if not others:
            raise ValueError("direct conversation has no other participant")
        if receiver is None:
            return others[0]
        return receiver

    def _validate_sender(self, conversation: Conversation, sender: str) -> None:
        if sender in {self._human_id, SYSTEM_SENDER}:
            return
        if sender not in conversation.participants:
            raise ValueError(
                f"sender {sender!r} is not a participant of "
                f"{conversation.conversation_id!r}"
            )
        if sender not in self._agents:
            raise ValueError(f"sender {sender!r} is not a registered agent")

    def _validate_receiver(self, conversation: Conversation, receiver: str) -> None:
        if receiver in {self._human_id, SYSTEM_SENDER}:
            return
        if receiver == conversation.conversation_id:
            return
        if receiver not in conversation.participants:
            raise ValueError(
                f"receiver {receiver!r} is not a participant of "
                f"{conversation.conversation_id!r}"
            )
        if receiver not in self._agents:
            raise ValueError(f"receiver {receiver!r} is not a registered agent")

    def _deliver(self, conversation: Conversation, message: Message) -> None:
        if conversation.conversation_type == ConversationType.GROUP:
            targets = [
                participant
                for participant in conversation.participants
                if participant in self._agents and participant != message.sender
            ]
        else:
            targets = [
                message.receiver
            ] if message.receiver in self._agents and message.receiver != message.sender else []

        for agent_id in targets:
            self._agents[agent_id].deliver(message)
