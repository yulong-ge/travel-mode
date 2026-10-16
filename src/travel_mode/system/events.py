"""Consume a launchd XPC event once; no resident listener or polling thread.

The global Block layout follows https://clang.llvm.org/docs/Block-ABI-Apple.html.
Only the private launch-event entry registers this process with libxpc.
"""

import ctypes as C

from ..automation import AutomationError
from ..i18n import t

STREAM = b"com.apple.notifyd.matching"
_live = []  # libxpc retains the global Block until process exit.


class EventError(AutomationError):
    pass


INVOKE = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)


class Descriptor(C.Structure):
    _fields_ = [("reserved", C.c_ulong), ("size", C.c_ulong)]


class Block(C.Structure):
    _fields_ = [
        ("isa", C.c_void_p),
        ("flags", C.c_int),
        ("reserved", C.c_int),
        ("invoke", INVOKE),
        ("descriptor", C.POINTER(Descriptor)),
    ]


def consume_once(timeout=10, *, library=None):
    """One native timed semaphore wait, only after launchd has queued an event."""
    if _live:
        raise EventError(t("automation.stream_registered"))
    lib = library or C.CDLL("/usr/lib/libSystem.B.dylib")
    for name, arguments, result in [
        ("dispatch_semaphore_create", [C.c_long], C.c_void_p),
        ("dispatch_semaphore_signal", [C.c_void_p], C.c_long),
        ("dispatch_semaphore_wait", [C.c_void_p, C.c_uint64], C.c_long),
        ("dispatch_time", [C.c_uint64, C.c_int64], C.c_uint64),
        ("xpc_set_event_stream_handler", [C.c_char_p, C.c_void_p, C.POINTER(Block)], None),
    ]:
        function = getattr(lib, name)
        function.argtypes, function.restype = arguments, result
    semaphore = lib.dispatch_semaphore_create(0)
    if not semaphore:
        raise EventError(t("automation.native_failed", detail="dispatch_semaphore_create"))
    callback = INVOKE(lambda block, event: lib.dispatch_semaphore_signal(semaphore))
    descriptor = Descriptor(0, C.sizeof(Block))
    isa = (
        C.addressof((C.c_void_p * 32).in_dll(lib, "_NSConcreteGlobalBlock"))
        if library is None
        else lib.global_block_address
    )
    block = Block(isa, 1 << 28, 0, callback, C.pointer(descriptor))
    _live.extend((lib, semaphore, callback, descriptor, block))
    lib.xpc_set_event_stream_handler(STREAM, None, C.byref(block))
    deadline = lib.dispatch_time(0, int(timeout * 1_000_000_000))
    if lib.dispatch_semaphore_wait(semaphore, deadline):
        raise EventError(t("automation.event_timeout", seconds=timeout))
