"""Documented native identities take precedence; fallback never weakens protection."""

import json
import sys
from types import SimpleNamespace

import pytest

from travel_mode.system import health_native, lsappinfo, workspace
from travel_mode.system.shell import ReadError

NATIVE = [sys.executable, "-m", "travel_mode.system.health_native", "foreground"]
IDENTITY = dict(pid=810, bundle_id="org.example.worker", bundle_path="/Applications/Worker.app")


@pytest.mark.parametrize("value", [IDENTITY, None])
def test_native_backend_available_and_nil_have_explicit_semantics(value):
    result = workspace.frontmost_application(SimpleNamespace(frontmost=lambda: value))
    assert result["source"] == "NSWorkspace"
    if value is None:
        assert result == dict(
            status="unavailable", source="NSWorkspace", reason="frontmostApplication returned nil"
        )
    else:
        assert result == dict(status="available", source="NSWorkspace", **IDENTITY)


def test_native_cli_uses_replaceable_backend(monkeypatch, capsys):
    monkeypatch.setattr(workspace, "WorkspaceBackend", lambda: SimpleNamespace(frontmost=lambda: IDENTITY))
    monkeypatch.setattr(sys, "argv", ["health_native", "foreground"])
    health_native.main()
    assert json.loads(capsys.readouterr().out) == dict(status="available", source="NSWorkspace", **IDENTITY)


def test_native_identity_needs_no_lsappinfo_read():
    calls = []

    def read(command):
        calls.append(command)
        assert command == NATIVE
        return json.dumps(dict(status="available", **IDENTITY))

    front = lsappinfo.foreground(read)
    assert front.as_dict() == dict(**IDENTITY, source="NSWorkspace", fallback_reason=None)
    assert calls == [NATIVE]


@pytest.mark.parametrize("failure", ["nil", "timeout", "malformed"])
def test_explicit_fallback_records_native_failure_and_source(failure):
    def read(command):
        if command == NATIVE:
            if failure == "timeout":
                raise ReadError("native foreground: timeout")
            return (
                '{"status":"unavailable","reason":"frontmostApplication returned nil"}'
                if failure == "nil"
                else "invalid JSON"
            )
        if command == ["lsappinfo", "front"]:
            return "ASN:0x0-0x1:"
        assert command[:4] == ["lsappinfo", "info", "-only", "pid,bundleid"]
        return '"pid"=358\n"CFBundleIdentifier"="com.apple.finder"\n'

    front = lsappinfo.foreground(read)
    assert front.source == "lsappinfo" and front.pid == 358
    assert front.fallback_reason
    if failure in {"nil", "timeout"}:
        assert failure in front.fallback_reason


def test_native_read_is_bounded_and_double_failure_keeps_both_causes(monkeypatch):
    calls = []

    def read(command, *, timeout):
        calls.append((command, timeout))
        raise ReadError("native timeout" if command == NATIVE else "lsappinfo not available")

    monkeypatch.setattr(lsappinfo, "_read", read)
    with pytest.raises(lsappinfo.ForegroundError, match=r"native timeout.*lsappinfo not available"):
        lsappinfo.foreground()
    assert all(timeout == 2 for _, timeout in calls)


def test_native_bundle_url_protects_target_even_without_bundle_id(tmp_path, fake_runner):
    from test_lsappinfo import machine

    from travel_mode.actions.apps import FreezeApp
    from travel_mode.system.shell import CmdResult

    path = machine(tmp_path, fake_runner)
    fake_runner.responses[tuple(NATIVE)] = CmdResult(
        json.dumps(dict(status="available", pid=811, bundle_id=None, bundle_path=path)), "", 0
    )
    with pytest.raises(PermissionError):
        FreezeApp(path).apply(None)
    assert not any(c[0] in {"kill", "osascript", "launchctl"} for c in fake_runner.calls)


def test_native_runtime_reads_documented_selectors_and_drains_pool():
    backend = workspace.WorkspaceBackend.__new__(workspace.WorkspaceBackend)
    backend.runtime = SimpleNamespace(objc_getClass=lambda name: name)
    calls = []
    values = {
        (b"NSAutoreleasePool", "new"): "pool",
        (b"NSWorkspace", "sharedWorkspace"): "workspace",
        ("workspace", "frontmostApplication"): "application",
        ("application", "processIdentifier"): 810,
        ("application", "bundleIdentifier"): "identifier",
        ("application", "bundleURL"): "url",
        ("url", "path"): "path",
        ("identifier", "UTF8String"): b"org.example.worker",
        ("path", "UTF8String"): b"/Applications/Worker.app",
        ("pool", "drain"): None,
    }

    def send(receiver, selector, *result_type):
        calls.append((receiver, selector))
        return values[receiver, selector]

    backend.send = send
    assert backend.frontmost() == IDENTITY
    assert calls[-1] == ("pool", "drain")
    del values["application", "bundleIdentifier"]
    with pytest.raises(KeyError):
        backend.frontmost()
    assert calls[-1] == ("pool", "drain")
