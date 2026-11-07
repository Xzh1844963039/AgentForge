"""Message bus routing, replay and conversation tests."""

from __future__ import annotations

import pytest

from agentforge.messaging import ConversationManager, MessageBus
from agentforge.models.common import HUMAN_SENDER, SYSTEM_SENDER
from agentforge.models.message import Message, MessageType
from agentforge.persistence import EventLogger, SQLiteStore


class DummyAgent:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.received: list[Message] = []

    def deliver(self, message: Message) -> None:
        self.received.append(message)


@pytest.fixture
def bus_setup(tmp_path):
    store = SQLiteStore(tmp_path / "messages.db")
    events = EventLogger(tmp_path / "events.jsonl")
    conversations = ConversationManager(store)
    bus = MessageBus(store, conversations, events)
    agents = {name: DummyAgent(name) for name in ("alpha", "beta")}
    for agent in agents.values():
        bus.register_agent(agent)
    yield store, events, conversations, bus, agents
    events.close()
    store.close()


def test_human_to_agent_direct_and_reply(bus_setup):
    store, events, conversations, bus, agents = bus_setup
    incoming = bus.send_direct(
        sender=HUMAN_SENDER,
        receiver="alpha",
        message_type=MessageType.DIRECT,
        content="hello",
    )
    assert agents["alpha"].received == [incoming]
    assert incoming.receiver == "alpha"

    reply = bus.send_direct(
        sender="alpha",
        receiver=HUMAN_SENDER,
        message_type=MessageType.DIRECT,
        content="hi human",
        in_reply_to=incoming.message_id,
    )
    assert reply.sender == "alpha"
    assert len(agents["alpha"].received) == 1  # replies to humans are not inboxed

    conversation = store.get_conversation(incoming.conversation_id)
    assert conversation is not None
    assert conversation.participants == ["human", "alpha"]
    transcript = bus.replay(incoming.conversation_id)
    assert [message.content for message in transcript] == ["hello", "hi human"]
    events.close()


def test_agent_to_agent_direct_delivery(bus_setup):
    store, events, conversations, bus, agents = bus_setup
    message = bus.send_direct(
        sender="alpha",
        receiver="beta",
        message_type=MessageType.TASK_REQUEST,
        content="please do x",
        task_id="task_1",
    )
    assert agents["beta"].received == [message]
    assert agents["alpha"].received == []
    assert message.task_id == "task_1"


def test_group_broadcast_and_agent_to_group(bus_setup):
    store, events, conversations, bus, agents = bus_setup
    group = conversations.create_group("G", ["alpha", "beta"])
    human_message = bus.send(
        sender=HUMAN_SENDER,
        conversation_id=group.conversation_id,
        message_type=MessageType.GROUP,
        content="goal",
    )
    assert human_message.receiver == group.conversation_id
    assert len(agents["alpha"].received) == 1
    assert len(agents["beta"].received) == 1

    agent_message = bus.send(
        sender="alpha",
        conversation_id=group.conversation_id,
        message_type=MessageType.DELEGATION,
        content="delegating",
    )
    assert len(agents["alpha"].received) == 1  # sender does not receive its own message
    assert len(agents["beta"].received) == 2
    assert agent_message.receiver == group.conversation_id
    assert len(bus.replay(group.conversation_id)) == 2


def test_system_message_can_be_sent_to_group(bus_setup):
    store, events, conversations, bus, agents = bus_setup
    group = conversations.create_group("G", ["alpha", "beta"])
    bus.send(
        sender=SYSTEM_SENDER,
        conversation_id=group.conversation_id,
        message_type=MessageType.SYSTEM,
        content="status",
    )
    assert len(agents["alpha"].received) == 1
    assert len(agents["beta"].received) == 1


def test_non_participant_sender_rejected(bus_setup):
    store, events, conversations, bus, agents = bus_setup
    group = conversations.create_group("G", ["alpha"])
    with pytest.raises(ValueError, match="not a participant"):
        bus.send(
            sender="beta",
            conversation_id=group.conversation_id,
            message_type=MessageType.GROUP,
            content="nope",
        )


def test_unknown_conversation_rejected(bus_setup):
    store, events, conversations, bus, agents = bus_setup
    with pytest.raises(KeyError):
        bus.send(
            sender=HUMAN_SENDER,
            conversation_id="missing",
            message_type=MessageType.GROUP,
            content="x",
        )


def test_direct_conversation_ids_are_deterministic(bus_setup):
    store, events, conversations, bus, agents = bus_setup
    first = conversations.get_or_create_direct("human", "alpha")
    second = conversations.get_or_create_direct("alpha", "human")
    assert first.conversation_id == second.conversation_id
    assert len(conversations.list()) == 1


def test_duplicate_delivery_is_ignored_by_agents():
    from agentforge.agents.factory import build_default_agents
    from agentforge.llm.mock import MockProvider
    from agentforge.memory import InMemoryMemoryStore
    from agentforge.tools import ToolRegistry

    agents = build_default_agents(
        provider=MockProvider(),
        memory=InMemoryMemoryStore(),
        tools=ToolRegistry(),
    )
    agent = agents[0]
    message = Message(
        conversation_id="c",
        sender="human",
        receiver=agent.agent_id,
        message_type=MessageType.DIRECT,
        content="hello",
    )
    agent.deliver(message)
    agent.deliver(message)
    assert agent.inbox_size == 1
    assert agent.take_message("other-task") is None
    assert agent.take_message() == message
