import asyncio

from app.events import Event, EventBus


def test_publish_reaches_all_subscribers():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish(Event("status", {"device_id": 1}))
    assert a.get_nowait().data == {"device_id": 1}
    assert b.get_nowait().kind == "status"


def test_slow_subscriber_drops_oldest():
    bus = EventBus(max_queue=2)
    q = bus.subscribe()
    for i in range(3):
        bus.publish(Event("tick", {"i": i}))
    assert [q.get_nowait().data["i"] for _ in range(2)] == [1, 2]
    assert q.empty()


def test_unsubscribe():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish(Event("x"))
    assert isinstance(q, asyncio.Queue) and q.empty()
