"""亮度能力选择；已选后端调用失败显式报错。"""

from __future__ import annotations

import ctypes
import re
import shutil
from dataclasses import dataclass

from ..i18n import t
from .shell import _read, get_runner, is_dry_run, run_out, run_rc, subprocess_runner

DISPLAY_SERVICES = "/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices"
CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"


@dataclass
class NativeBrightness:
    services: object
    graphics: object

    @classmethod
    def load(cls):
        services = ctypes.CDLL(DISPLAY_SERVICES)
        graphics = ctypes.CDLL(CORE_GRAPHICS)
        graphics.CGMainDisplayID.argtypes = []
        graphics.CGMainDisplayID.restype = ctypes.c_uint32
        services.DisplayServicesGetBrightness.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_float)]
        services.DisplayServicesGetBrightness.restype = ctypes.c_int
        services.DisplayServicesSetBrightness.argtypes = [ctypes.c_uint32, ctypes.c_float]
        services.DisplayServicesSetBrightness.restype = ctypes.c_int
        return cls(services, graphics)

    def get(self) -> float:
        value = ctypes.c_float()
        rc = self.services.DisplayServicesGetBrightness(self.graphics.CGMainDisplayID(), ctypes.byref(value))
        if rc:
            raise RuntimeError(t("brightness.failed", operation="DisplayServicesGetBrightness", rc=rc))
        if not 0 <= value.value <= 1:
            raise RuntimeError(
                t(
                    "brightness.failed",
                    operation="DisplayServicesGetBrightness",
                    rc=f"invalid value {value.value:g}",
                )
            )
        return value.value

    def set(self, value: float) -> tuple[str, int]:
        rc = self.services.DisplayServicesSetBrightness(
            self.graphics.CGMainDisplayID(), ctypes.c_float(value)
        )
        if rc:
            raise RuntimeError(t("brightness.failed", operation="DisplayServicesSetBrightness", rc=rc))
        return "", 0


class CliBrightness:
    def get(self) -> float:
        output, rc = run_out(["brightness", "-l"], timeout=10)
        match = re.search(r"brightness ([\d.]+)", output)
        if rc or match is None:
            raise RuntimeError(t("brightness.failed", operation="brightness -l", rc=rc))
        return float(match[1])

    def set(self, value: float):
        _, rc = run_rc(["brightness", str(value)])
        if rc:
            raise RuntimeError(t("brightness.failed", operation="brightness", rc=rc))
        return "", 0


def select_backend():
    try:
        native = NativeBrightness.load()
        value = native.get()
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise RuntimeError(
                t("brightness.failed", operation="DisplayServicesGetBrightness", rc=f"invalid value {value}")
            )
        return native
    except (OSError, AttributeError, RuntimeError):
        pass
    if shutil.which("brightness"):
        cli = CliBrightness()
        try:
            cli.get()
        except RuntimeError:
            return None
        return cli
    return None


def available() -> bool:
    return select_backend() is not None


def get_brightness() -> str:
    backend = select_backend()
    if backend is None:
        raise RuntimeError(t("brightness.unavailable"))
    return str(backend.get())


def set_brightness(value: float | str) -> tuple[str, int]:
    number = float(value)
    if not 0 <= number <= 1:
        raise ValueError(t("brightness.invalid_value"))
    if is_dry_run() or get_runner() is not subprocess_runner:
        return run_rc(["brightness", str(number)])
    backend = select_backend()
    if backend is None:
        raise RuntimeError(t("brightness.unavailable"))
    from ..actions.journal import complete_command, prepare_command

    command = ["brightness", str(number)]
    prepared = prepare_command(command)
    result = backend.set(number)
    if prepared:
        complete_command(command, result[1])
    return result


def current_brightness():
    if get_runner() is subprocess_runner and not is_dry_run():
        from ..system.brightness import get_brightness

        return get_brightness()
    out = _read(["brightness", "-l"], timeout=10)
    m = re.search(r"brightness ([\d.]+)", out)
    if m and 0 <= float(m.group(1)) <= 1:
        return m.group(1)
    raise RuntimeError(t("safety.invalid_status", command="brightness -l", detail=out.strip()))
