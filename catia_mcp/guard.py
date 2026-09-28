"""Runtime guard rails for unattended CATIA sessions (all opt-in or safe by default).

CATIA's COM server is single-threaded and any modal dialog freezes every call silently.
An agent that runs alone for hours needs three protections, each learned the hard way:

* **Popup watchdog** (``CATIA_MCP_WATCHDOG``, default on): a daemon thread looks for CATIA
  dialogs every 2 s, logs their text, and closes ONLY information/error boxes that have a
  single OK/Close button. A question (Yes/No, Save?) is never answered blindly: it is only
  reported.
* **Cross-process lock** (``CATIA_MCP_LOCK``, default off): several agents or scripts driving the
  same CATIA queue up instead of interleaving calls.
* **Hang guard** (``CATIA_MCP_HANG_SECONDS``, default 0 = off; ``CATIA_MCP_HANG_KILL=1`` to also
  kill CATIA): a call that exceeds the delay is logged; with kill enabled CATIA is terminated
  so the blocked COM call fails and the session can restart (documents saved to disk are safe,
  unsaved work is lost: hence off by default).
"""

from __future__ import annotations

import datetime
import logging
import subprocess
import sys
import threading
import time
from typing import Callable

from catia_mcp import paths

logger = logging.getLogger("catia_mcp.guard")

_SAFE_BUTTONS = {"ok", "fermer", "close"}
_lock_handle = None
_watchdog_stop: threading.Event | None = None


# ── cross-process lock ──────────────────────────────────────────────────────────────────


def acquire_lock(verbose: bool = True) -> bool:
    """Block until this process owns the CATIA lock. Released automatically at process exit,
    even after a crash. No-op (returns False) where msvcrt is unavailable."""
    global _lock_handle
    if _lock_handle is not None:
        return True
    try:
        import msvcrt
    except ImportError:
        return False
    handle = open(paths.lock_file(), "a+")  # noqa: SIM115 - kept open for the process lifetime
    started, told = time.time(), False
    while True:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            if verbose and not told:
                logger.warning("CATIA is used by another process: waiting for the lock...")
                told = True
            time.sleep(2)
    _lock_handle = handle
    if told:
        logger.info("Lock acquired after %.0f s", time.time() - started)
    return True


# ── popup watchdog ──────────────────────────────────────────────────────────────────────


def _catia_dialogs() -> list[int]:
    import win32gui
    import win32process

    from catia_mcp.capture import _catia_pids

    pids = _catia_pids()
    found: list[int] = []

    def cb(hwnd: int, _: object) -> None:
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "#32770":
            if win32process.GetWindowThreadProcessId(hwnd)[1] in pids:
                found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found


def _describe(hwnd: int):
    import win32gui

    texts: list[str] = []
    buttons: list[tuple[int, str]] = []

    def cb(child: int, _: object) -> None:
        cls = win32gui.GetClassName(child)
        txt = win32gui.GetWindowText(child).strip()
        if cls == "Button" and win32gui.IsWindowVisible(child):
            buttons.append((child, txt))
        elif txt:
            texts.append(txt)

    win32gui.EnumChildWindows(hwnd, cb, None)
    return win32gui.GetWindowText(hwnd), texts, buttons


def is_safe_to_close(button_labels: list[str]) -> bool:
    """True only for a dialog whose ONLY button is OK/Close (never a question)."""
    labels = [b.replace("&", "").strip().lower() for b in button_labels]
    return len(labels) == 1 and labels[0] in _SAFE_BUTTONS


def _watch(stop: threading.Event, report: Callable[[str], None]) -> None:
    import win32con
    import win32gui

    seen: set = set()
    while not stop.wait(2.0):
        try:
            for hwnd in _catia_dialogs():
                title, texts, buttons = _describe(hwnd)
                labels = [b[1] for b in buttons]
                closable = is_safe_to_close(labels)
                key = (hwnd, title, tuple(texts))
                if key in seen and not closable:
                    continue
                seen.add(key)
                msg = (
                    f"[CATIA POPUP] '{title}': {' / '.join(texts)[:400]}; buttons {labels} -> "
                    + ("closed automatically (OK)" if closable else "LEFT OPEN (question): needs a human")
                )
                report(msg)
                with paths.popup_log().open("a", encoding="utf-8") as f:
                    f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
                if closable:
                    win32gui.SendMessage(buttons[0][0], win32con.BM_CLICK, 0, 0)
                    time.sleep(0.5)
        except Exception:  # a watchdog must never die or raise
            pass


def start_watchdog(report: Callable[[str], None] | None = None) -> bool:
    """Start the popup watchdog once. Returns False when unavailable (non-Windows, no pywin32)."""
    global _watchdog_stop
    if _watchdog_stop is not None:
        return True
    try:
        import win32gui  # noqa: F401
    except ImportError:
        return False
    _watchdog_stop = threading.Event()
    threading.Thread(
        target=_watch, args=(_watchdog_stop, report or logger.warning), daemon=True
    ).start()
    return True


# ── hang guard ──────────────────────────────────────────────────────────────────────────


class HangGuard:
    """Context manager: flag (and optionally kill CATIA) when one call exceeds ``seconds``."""

    def __init__(self, seconds: float, label: str, kill: bool = False) -> None:
        self.seconds = seconds
        self.label = label
        self.kill = kill
        self.fired = False
        self._timer: threading.Timer | None = None

    def _fire(self) -> None:
        self.fired = True
        logger.error("HANG: %s has been blocking CATIA for %.0f s", self.label, self.seconds)
        if self.kill and sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "CNEXT.exe"], capture_output=True)

    def __enter__(self) -> HangGuard:
        if self.seconds and self.seconds > 0:
            self._timer = threading.Timer(self.seconds, self._fire)
            self._timer.daemon = True
            self._timer.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._timer is not None:
            self._timer.cancel()


def hang_seconds() -> float:
    import os

    try:
        return float(os.environ.get("CATIA_MCP_HANG_SECONDS", "0"))
    except ValueError:
        return 0.0
