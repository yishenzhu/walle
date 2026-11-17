"""断连补发缓冲：序号、按断点回放、容量上限。"""

from __future__ import annotations

from ..core.link import BUFFER_LIMIT, SessionLink
from ..spec import Delta


class FakeTransport:
    def __init__(self):
        self.seen: list = []

    async def notify(self, notification):
        self.seen.append(notification)


async def test_seq_increases_per_notification():
    link = SessionLink("s1")

    await link.notify(Delta(delta="a"))
    await link.notify(Delta(delta="b"))

    assert link.seq == 2


async def test_replay_skips_already_seen():
    link = SessionLink("s1")
    await link.notify(Delta(delta="a"))
    await link.notify(Delta(delta="b"))
    await link.notify(Delta(delta="c"))

    transport = FakeTransport()
    await link.replay(transport, last_seq=2)

    assert [item.delta for item in transport.seen] == ["c"]


async def test_replay_without_seq_sends_all():
    link = SessionLink("s1")
    await link.notify(Delta(delta="a"))
    await link.notify(Delta(delta="b"))

    transport = FakeTransport()
    await link.replay(transport)

    assert [item.delta for item in transport.seen] == ["a", "b"]


async def test_buffer_drops_oldest_when_full():
    link = SessionLink("s1")
    for index in range(BUFFER_LIMIT + 50):
        await link.notify(Delta(delta=str(index)))

    transport = FakeTransport()
    await link.replay(transport)

    assert len(transport.seen) == BUFFER_LIMIT
    assert transport.seen[0].delta == "50"
    assert transport.seen[-1].delta == str(BUFFER_LIMIT + 49)
