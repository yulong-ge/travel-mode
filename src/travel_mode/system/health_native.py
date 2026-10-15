"""Short-lived, read-only native probes, isolated so callers can enforce a timeout."""

import ctypes
import json
import sys


def displays():
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cg.CGGetOnlineDisplayList.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    cg.CGGetOnlineDisplayList.restype = ctypes.c_int
    cg.CGDisplayIsBuiltin.argtypes = [ctypes.c_uint32]
    cg.CGDisplayIsBuiltin.restype = ctypes.c_uint32
    ids = (ctypes.c_uint32 * 64)()
    count = ctypes.c_uint32()
    rc = cg.CGGetOnlineDisplayList(64, ids, ctypes.byref(count))
    if rc or count.value >= 64:
        raise RuntimeError(f"CGGetOnlineDisplayList: rc={rc}, count={count.value}")
    return [{"id": ids[i], "builtin": bool(cg.CGDisplayIsBuiltin(ids[i]))} for i in range(count.value)]


def main():
    if sys.argv[1:] == ["displays"]:
        result = displays()
    elif sys.argv[1:] == ["brightness"]:
        from .brightness import NativeBrightness

        result = NativeBrightness.load().get()
    else:
        raise ValueError("expected displays or brightness")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, AttributeError) as exc:
        sys.exit(str(exc))
