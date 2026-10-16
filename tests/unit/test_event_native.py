"""The native bridge consumes one queued event with a single bounded wait."""

import ctypes

import pytest

from travel_mode.system import events


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class Library:
    global_block_address = 1234

    def __init__(self, *, timeout=False):
        self.calls = []
        self.block = None
        self.dispatch_semaphore_create = Function(lambda value: 42)
        self.dispatch_semaphore_signal = Function(
            lambda semaphore: self.calls.append(("signal", semaphore)) or 0
        )
        self.dispatch_time = Function(
            lambda base, nanoseconds: self.calls.append(("deadline", base, nanoseconds)) or 99
        )
        self.dispatch_semaphore_wait = Function(
            lambda semaphore, deadline: self.calls.append(("wait", semaphore, deadline)) or int(timeout)
        )
        self.xpc_set_event_stream_handler = Function(self.register)

    def register(self, stream, queue, block):
        self.calls.append(("register", stream, queue))
        self.block = ctypes.cast(block, ctypes.POINTER(events.Block)).contents
        self.block.invoke(None, None)


def test_global_block_layout_and_single_bounded_xpc_consumption(monkeypatch):
    monkeypatch.setattr(events, "_live", [])
    lib = Library()
    events.consume_once(library=lib)
    assert lib.calls == [
        ("register", events.STREAM, None),
        ("signal", 42),
        ("deadline", 0, 10_000_000_000),
        ("wait", 42, 99),
    ]
    assert lib.block.flags == 1 << 28
    assert lib.block.descriptor.contents.size == ctypes.sizeof(events.Block)
    assert len(events._live) == 5
    with pytest.raises(events.EventError):
        events.consume_once(library=lib)
    assert len(lib.calls) == 4


def test_native_wait_timeout_is_explicit_and_has_no_retry(monkeypatch):
    monkeypatch.setattr(events, "_live", [])
    lib = Library(timeout=True)
    with pytest.raises(events.EventError, match="10"):
        events.consume_once(library=lib)
    assert sum(call[0] == "wait" for call in lib.calls) == 1
