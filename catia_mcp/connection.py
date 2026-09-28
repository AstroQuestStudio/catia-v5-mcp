"""CATIA V5 COM Connection Manager.

Manages the connection to CATIA V5 via Windows COM Automation API (win32com).
Supports connecting to a running instance or launching a new one.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("catia_mcp")

# COM imports are deferred to runtime (Windows only)
try:
    import pythoncom
    import win32com.client

    HAS_COM = True
except ImportError:
    HAS_COM = False


class CATIAConnection:
    """Manages connection to CATIA V5 via COM Automation."""

    # CATIA V5 COM ProgID
    CATIA_PROGID = "CATIA.Application"

    def __init__(self) -> None:
        self.app: Any | None = None
        self._initialized_com = False
        # Name of the body last activated via catia_activate_body. Cached here
        # (not read back from CATIA) because CATIA moves Part.InWorkObject to
        # point at the newest FEATURE right after it's created, not the body —
        # so re-deriving "the active body" from InWorkObject only works for the
        # first feature after activation. See get_active_part_body().
        self.active_body_name: str | None = None

    @property
    def is_connected(self) -> bool:
        """Check if we have an active CATIA connection."""
        if self.app is None:
            return False
        try:
            # Try accessing a property to verify the connection is alive
            _ = self.app.Caption
            return True
        except Exception:
            self.app = None
            return False

    def connect(self) -> str:
        """Connect to CATIA V5. Tries running instance first, then launches new one.

        Returns a status message string.
        """
        if os.environ.get("CATIA_MCP_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
            # Hard safety switch for tests, CI and unlicensed machines: never attach to or
            # launch CATIA, whatever tool is called.
            raise RuntimeError("CATIA_MCP_OFFLINE is set: connecting to CATIA is disabled.")

        if not HAS_COM:
            raise RuntimeError(
                "pywin32 is not installed. Install it with: pip install pywin32\n"
                "Note: This MCP server requires Windows with CATIA V5 installed."
            )

        if self.is_connected:
            version = self._get_version()
            return f"Already connected to CATIA V5 ({version})"

        # Initialize COM for this thread
        if not self._initialized_com:
            pythoncom.CoInitialize()
            self._initialized_com = True

        # Phase 1: Try to attach to a running CATIA instance
        try:
            self.app = win32com.client.GetActiveObject(self.CATIA_PROGID)
            # Self-heal: a script killed while display_batch() had frozen the
            # viewer leaves CATIA with RefreshDisplay = False (seen live) —
            # the user then sees a frozen 3D view. Always start from a live one.
            try:
                self.app.RefreshDisplay = True
            except Exception:
                pass
            # Report screenshots must show the tree unfolded: « Développement
            # automatique » (Options > Affichage > Arbre) unfolds each new feature
            # with its sketch. Session-only (no SaveRepository), proven live R19.
            try:
                self.app.SettingControllers.Item("CATCafTreeVizManipSettingCtrl").AutoExpandActivation = True
            except Exception:
                pass
            version = self._get_version()
            logger.info("Connected to running CATIA V5 instance (%s)", version)
            return f"Connected to running CATIA V5 instance ({version})"
        except Exception:
            logger.info("No running CATIA instance found, launching new one...")

        # Phase 2: Launch a new CATIA instance
        try:
            self.app = win32com.client.Dispatch(self.CATIA_PROGID)
            self.app.Visible = True
            version = self._get_version()
            logger.info("Launched new CATIA V5 instance (%s)", version)
            return f"Launched new CATIA V5 instance ({version})"
        except Exception as e:
            self.app = None
            raise RuntimeError(
                f"Failed to connect to CATIA V5: {e}\n"
                "Make sure CATIA V5 is installed and licensed on this machine."
            ) from e

    def disconnect(self) -> str:
        """Disconnect from CATIA V5 (does not close CATIA)."""
        if self.app is not None:
            self.app = None
        if self._initialized_com:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            self._initialized_com = False
        return "Disconnected from CATIA V5"

    def ensure_connected(self) -> None:
        """Ensure we have an active CATIA connection, connecting if needed."""
        if not self.is_connected:
            self.connect()

    def _get_version(self) -> str:
        """Get CATIA version string."""
        try:
            # CATIA V5 exposes SystemService.Environ or Caption
            caption = self.app.Caption
            return caption if caption else "unknown version"
        except Exception:
            return "unknown version"

    # ── Helper properties for quick access to CATIA objects ──

    @property
    def documents(self) -> Any:
        """Get the CATIA Documents collection."""
        self.ensure_connected()
        return self.app.Documents

    @property
    def active_document(self) -> Any:
        """Get the active CATIA document."""
        self.ensure_connected()
        try:
            return self.app.ActiveDocument
        except Exception:
            raise RuntimeError("No active document in CATIA. Create or open a document first.")

    @property
    def active_window(self) -> Any:
        """Get the active window (CATIA V5 has no ActiveEditor)."""
        self.ensure_connected()
        return self.app.ActiveWindow

    @property
    def hso(self) -> Any:
        """Get the Highlighted Set of Objects (selection)."""
        self.ensure_connected()
        return self.active_document.Selection

    def refresh_display(self) -> None:
        """Deliberately a no-op now.

        It used to Reframe the camera after EVERY operation: a full re-render
        plus a view jump each time. The display is refreshed once at the end of
        each tool call instead (see display_batch); catia_fit_all still reframes
        on demand.
        """

    @contextmanager
    def display_batch(self) -> Iterator[None]:
        """Freeze 3D redraws while a tool runs, always restoring them after.

        CATIA's COM server is single-threaded (STA): calls cannot be
        parallelised, so the safe speed-up is to stop the viewer from redrawing
        after every intermediate COM call (the standard CATIA macro trick,
        CATIA.RefreshDisplay = False). Restored in `finally`, even if the tool
        raises, so CATIA is never left with a frozen display.
        """
        app = self.app
        previous = True
        try:
            previous = bool(app.RefreshDisplay)
            app.RefreshDisplay = False
        except Exception:
            app = None
        try:
            yield
        finally:
            if app is not None:
                try:
                    app.RefreshDisplay = previous
                except Exception:
                    pass

    # ── Document type detection ──

    def get_active_part(self) -> Any:
        """Get the Part object from the active PartDocument."""
        doc = self.active_document
        try:
            return doc.Part
        except Exception:
            raise RuntimeError(
                "Active document is not a Part document. "
                "Open or create a Part document first."
            )

    def get_active_product(self) -> Any:
        """Get the Product object from the active ProductDocument."""
        doc = self.active_document
        try:
            return doc.Product
        except Exception:
            raise RuntimeError(
                "Active document is not a Product document. "
                "Open or create an Assembly (Product) document first."
            )

    def get_active_part_body(self) -> Any:
        """Get the body that new features should be created in.

        Live-tested 2026-09-28: CATIA sets Part.InWorkObject to a Body right
        after "Define In Work Object" (GUI) or catia_activate_body (MCP), but
        the FIRST feature created afterwards (Pad, Pocket, ...) moves
        InWorkObject to point at that new FEATURE instead — so deriving "the
        active body" from InWorkObject only ever works for one feature. Every
        subsequent sketch/feature would silently fall back to MainBody, which
        defeats the whole point of activating a body.

        Fix: catia_activate_body caches the body's name in
        self.active_body_name; this method re-resolves that name against
        Part.Bodies on every call (never caches the COM object itself, since
        it can go stale across document changes) and only falls back to
        InWorkObject / MainBody when no cached name is set or it no longer
        resolves (e.g. after the body was deleted).
        """
        part = self.get_active_part()

        if self.active_body_name:
            try:
                bodies = part.Bodies
                for i in range(1, bodies.Count + 1):
                    body = bodies.Item(i)
                    if body.Name == self.active_body_name:
                        return body
            except Exception:
                pass
            # Cached name doesn't resolve anymore (renamed/deleted, or we're
            # now in a different document) — stop trusting it.
            self.active_body_name = None

        try:
            in_work = part.InWorkObject
            if in_work is not None:
                _ = in_work.Shapes  # raises if in_work isn't a Body
                return in_work
        except Exception:
            pass
        return part.MainBody

    def get_origin_elements(self) -> dict[str, Any]:
        """Get the origin planes (XY, YZ, ZX) from the active Part."""
        part = self.get_active_part()
        origin = part.OriginElements
        return {
            "xy": origin.PlaneXY,
            "yz": origin.PlaneYZ,
            "zx": origin.PlaneZX,
        }
