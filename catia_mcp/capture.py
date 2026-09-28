"""Capture the CATIA main window — even when it is behind other windows.

The first version BitBlt-copied the window's screen area: when CATIA was
covered (by a game, a browser...) it saved whatever was on top instead of
CATIA (seen live 2026-09-28). PrintWindow with PW_RENDERFULLCONTENT asks the
window to render ITSELF into our bitmap, so occlusion does not matter. The
window is found through the CNEXT.exe process, not a title substring (any
browser tab titled "...CATIA..." used to match).
"""

from __future__ import annotations

import ctypes
import subprocess
from pathlib import Path

PW_RENDERFULLCONTENT = 0x00000002


def _catia_pids() -> set[int]:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq CNEXT.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, creationflags=0x08000000,  # CREATE_NO_WINDOW
    ).stdout
    return {int(line.split('","')[1]) for line in out.strip().splitlines() if line.startswith('"')}


def find_catia_window() -> int | None:
    """Handle of CATIA's main frame window (largest visible top-level window of CNEXT)."""
    import win32gui
    import win32process

    pids = _catia_pids()
    if not pids:
        return None
    found: list[tuple[int, int]] = []

    def cb(hwnd: int, _: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in pids and win32gui.GetWindowText(hwnd).startswith("CATIA"):
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            found.append(((right - left) * (bottom - top), hwnd))

    win32gui.EnumWindows(cb, None)
    return max(found)[1] if found else None


def _restore_behind(hwnd: int) -> None:
    """Un-minimise CATIA WITHOUT stealing focus or covering the user's windows.

    A minimised window renders nothing (PrintWindow gives a blank bitmap), and the
    user often minimises CATIA while a long job runs (seen live). SW_SHOWNOACTIVATE
    restores without activation, HWND_BOTTOM sends it behind everything; if it had
    been maximised, it is re-sized to the monitor work area (SW_MAXIMIZE would
    activate it)."""
    import time

    import win32api
    import win32con
    import win32gui

    placement = win32gui.GetWindowPlacement(hwnd)
    was_maximised = bool(placement[0] & win32con.WPF_RESTORETOMAXIMIZED)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    flags = win32con.SWP_NOACTIVATE | win32con.SWP_NOOWNERZORDER
    if was_maximised:
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        win32gui.SetWindowPos(hwnd, win32con.HWND_BOTTOM, left, top, right - left, bottom - top, flags)
    else:
        win32gui.SetWindowPos(hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                              flags | win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
    time.sleep(1.0)  # let the 3D viewer repaint at its new size


def capture_catia_window(output: str | Path) -> str:
    """Save a PNG of the whole CATIA window (tree + 3D view). Raises on failure."""
    import win32gui
    import win32ui
    from PIL import Image

    hwnd = find_catia_window()
    if hwnd is None:
        raise RuntimeError("CATIA window not found (is CATIA running?).")
    if win32gui.IsIconic(hwnd):
        _restore_behind(hwnd)
        if win32gui.IsIconic(hwnd):
            raise RuntimeError("CATIA is minimised and could not be restored.")

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bitmap)
        ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not ok:
            raise RuntimeError("PrintWindow failed on the CATIA window.")
        info = bitmap.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                               bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        img.save(output, "PNG")
        return str(output)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
