"""Comunicazione bidirezionale con LEGO Mario / Luigi / Peach via Bluetooth LE.

`mario.lwp3` e `mario.codes` non hanno dipendenze; `MarioDevice` (che usa bleak)
viene importato solo quando serve, cosi' i test offline girano anche senza bleak.
"""
from . import codes, lwp3  # noqa: F401

__version__ = "0.1.0"
__all__ = ["MarioDevice", "LWPError", "lwp3", "codes"]


def __getattr__(name: str):
    if name in ("MarioDevice", "LWPError"):
        from . import device
        return getattr(device, name)
    raise AttributeError(f"module 'mario' has no attribute {name!r}")
