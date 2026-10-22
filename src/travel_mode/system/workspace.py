"""Read NSWorkspace in a disposable native probe, never on the TUI event loop.

https://developer.apple.com/documentation/appkit/nsworkspace/frontmostapplication
https://developer.apple.com/documentation/appkit/nsrunningapplication
"""

import ctypes as C


class WorkspaceBackend:
    def __init__(self):
        self.appkit = C.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        self.runtime = C.CDLL("/usr/lib/libobjc.A.dylib")
        self.runtime.objc_getClass.argtypes = [C.c_char_p]
        self.runtime.objc_getClass.restype = C.c_void_p
        self.runtime.sel_registerName.argtypes = [C.c_char_p]
        self.runtime.sel_registerName.restype = C.c_void_p

    def send(self, receiver, selector, result=C.c_void_p):
        function = C.CFUNCTYPE(result, C.c_void_p, C.c_void_p)(("objc_msgSend", self.runtime))
        return function(receiver, self.runtime.sel_registerName(selector.encode("ascii")))

    def string(self, receiver):
        return self.send(receiver, "UTF8String", C.c_char_p).decode("utf-8") if receiver else None

    def frontmost(self):
        pool = self.send(self.runtime.objc_getClass(b"NSAutoreleasePool"), "new")
        if not pool:
            raise RuntimeError("NSAutoreleasePool: allocation failed")
        try:
            workspace = self.send(self.runtime.objc_getClass(b"NSWorkspace"), "sharedWorkspace")
            if not workspace:
                raise RuntimeError("NSWorkspace.sharedWorkspace is unavailable")
            application = self.send(workspace, "frontmostApplication")
            if not application:
                return None
            url = self.send(application, "bundleURL")
            return dict(
                pid=self.send(application, "processIdentifier", C.c_int),
                bundle_id=self.string(self.send(application, "bundleIdentifier")),
                bundle_path=self.string(self.send(url, "path")) if url else None,
            )
        finally:
            self.send(pool, "drain", None)


def frontmost_application(backend=None):
    row = (backend or WorkspaceBackend()).frontmost()
    if row is None:
        return dict(status="unavailable", source="NSWorkspace", reason="frontmostApplication returned nil")
    return dict(status="available", source="NSWorkspace", **row)
