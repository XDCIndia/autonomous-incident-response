"""Tests for the in-process EventBus that powers the WebSocket live-timeline
stream. Not authored by Platform/Integration originally, but it's the core
of the "WebSocket/SSE live updates" line item, so it gets the same scrutiny
as everything else in this role."""

from __future__ import annotations

import asyncio

import pytest

from backend.contracts import PipelineStage, TimelineEvent
from backend.platform.events import EventBus, get_event_bus


@pytest.fixture
def bus():
    return EventBus()


class TestSubscribePublish:
    @pytest.mark.asyncio
    async def test_subscriber_receives_published_event(self, bus):
        queue = bus.subscribe("inc-1")
        event = TimelineEvent(stage=PipelineStage.DETECTION, status="completed", message="hello")
        await bus.publish("inc-1", event)

        data = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert data["message"] == "hello"
        assert data["stage"] == "detection"

    @pytest.mark.asyncio
    async def test_timestamp_serialized_to_iso_string(self, bus):
        queue = bus.subscribe("inc-1")
        event = TimelineEvent(stage=PipelineStage.DETECTION, message="x")
        await bus.publish("inc-1", event)
        data = await queue.get()
        assert isinstance(data["timestamp"], str)

    @pytest.mark.asyncio
    async def test_publish_to_incident_with_no_subscribers_does_not_raise(self, bus):
        event = TimelineEvent(stage=PipelineStage.DETECTION, message="no one listening")
        await bus.publish("nobody-subscribed", event)  # must not raise

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_receive_the_same_event(self, bus):
        q1 = bus.subscribe("inc-1")
        q2 = bus.subscribe("inc-1")
        q3 = bus.subscribe("inc-1")
        event = TimelineEvent(stage=PipelineStage.DETECTION, message="broadcast")
        await bus.publish("inc-1", event)

        for q in (q1, q2, q3):
            data = await asyncio.wait_for(q.get(), timeout=1.0)
            assert data["message"] == "broadcast"

    @pytest.mark.asyncio
    async def test_events_for_different_incidents_do_not_cross_over(self, bus):
        q_a = bus.subscribe("inc-a")
        q_b = bus.subscribe("inc-b")
        await bus.publish("inc-a", TimelineEvent(stage=PipelineStage.DETECTION, message="for-a"))

        data = await asyncio.wait_for(q_a.get(), timeout=1.0)
        assert data["message"] == "for-a"
        assert q_b.empty()

    @pytest.mark.asyncio
    async def test_events_preserve_publish_order(self, bus):
        queue = bus.subscribe("inc-1")
        for i in range(20):
            await bus.publish("inc-1", TimelineEvent(stage=PipelineStage.DETECTION, message=f"e{i}"))

        received = [await queue.get() for _ in range(20)]
        assert [r["message"] for r in received] == [f"e{i}" for i in range(20)]


class TestUnsubscribe:
    @pytest.mark.asyncio
    async def test_unsubscribed_queue_no_longer_receives_events(self, bus):
        queue = bus.subscribe("inc-1")
        bus.unsubscribe("inc-1", queue)
        await bus.publish("inc-1", TimelineEvent(stage=PipelineStage.DETECTION, message="missed"))
        assert queue.empty()

    def test_unsubscribe_unknown_incident_does_not_raise(self, bus):
        fake_queue = asyncio.Queue()
        bus.unsubscribe("never-subscribed", fake_queue)  # must not raise

    def test_unsubscribe_unknown_queue_for_known_incident_does_not_raise(self, bus):
        bus.subscribe("inc-1")
        other_queue = asyncio.Queue()
        bus.unsubscribe("inc-1", other_queue)  # not one of the real subscribers

    @pytest.mark.asyncio
    async def test_unsubscribing_one_of_two_leaves_the_other_intact(self, bus):
        q1 = bus.subscribe("inc-1")
        q2 = bus.subscribe("inc-1")
        bus.unsubscribe("inc-1", q1)
        await bus.publish("inc-1", TimelineEvent(stage=PipelineStage.DETECTION, message="still here"))

        assert q1.empty()
        data = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert data["message"] == "still here"


class TestGetNextEvent:
    @pytest.mark.asyncio
    async def test_returns_event_when_available(self, bus):
        queue = bus.subscribe("inc-1")
        await bus.publish("inc-1", TimelineEvent(stage=PipelineStage.DETECTION, message="fast"))
        data = await bus.get_next_event("inc-1", queue, timeout=1.0)
        assert data["message"] == "fast"

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, bus):
        queue = bus.subscribe("inc-1")
        data = await bus.get_next_event("inc-1", queue, timeout=0.1)
        assert data is None

    @pytest.mark.asyncio
    async def test_timeout_does_not_lose_a_later_event(self, bus):
        """A get_next_event() timeout must not consume/drop an event that
        arrives right after — this is exactly the WS handler's ping/data loop."""
        queue = bus.subscribe("inc-1")
        first = await bus.get_next_event("inc-1", queue, timeout=0.1)
        assert first is None

        await bus.publish("inc-1", TimelineEvent(stage=PipelineStage.DETECTION, message="arrived late"))
        second = await bus.get_next_event("inc-1", queue, timeout=1.0)
        assert second["message"] == "arrived late"


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_publishes_all_delivered(self, bus):
        queue = bus.subscribe("inc-1")
        await asyncio.gather(*[
            bus.publish("inc-1", TimelineEvent(stage=PipelineStage.DETECTION, message=f"e{i}"))
            for i in range(30)
        ])
        received = set()
        for _ in range(30):
            data = await asyncio.wait_for(queue.get(), timeout=1.0)
            received.add(data["message"])
        assert received == {f"e{i}" for i in range(30)}

    @pytest.mark.asyncio
    async def test_many_incidents_many_subscribers_no_cross_contamination(self, bus):
        incident_ids = [f"inc-{i}" for i in range(10)]
        queues = {iid: bus.subscribe(iid) for iid in incident_ids}

        await asyncio.gather(*[
            bus.publish(iid, TimelineEvent(stage=PipelineStage.DETECTION, message=f"msg-for-{iid}"))
            for iid in incident_ids
        ])

        for iid, queue in queues.items():
            data = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert data["message"] == f"msg-for-{iid}"


class TestSingleton:
    def test_get_event_bus_returns_same_instance(self):
        import backend.platform.events as events_module

        events_module._event_bus = None
        a = get_event_bus()
        b = get_event_bus()
        assert a is b
        events_module._event_bus = None
