"""Document management tools for CATIA V5.

Create, open, save, close, and list documents (Part, Product, Drawing).
"""

from __future__ import annotations

import json
from typing import Any

from catia_mcp.connection import CATIAConnection


class DocumentTools:
    """Tools for managing CATIA V5 documents."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_connect",
                "description": (
                    "Connect to CATIA V5. Attaches to a running instance or launches a new one. "
                    "Must be called before any other CATIA tool."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_disconnect",
                "description": "Disconnect from CATIA V5 (does not close CATIA).",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_new_part",
                "description": (
                    "Create a new empty Part document in CATIA V5. "
                    "A Part is used for single-body 3D modeling (sketches + features)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Optional name for the part",
                        },
                    },
                },
            },
            {
                "name": "catia_new_product",
                "description": (
                    "Create a new empty Product (assembly) document in CATIA V5. "
                    "A Product is used to assemble multiple Parts together."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Optional name for the product/assembly",
                        },
                    },
                },
            },
            {
                "name": "catia_open_document",
                "description": "Open an existing CATIA document from a file path (.CATPart, .CATProduct, .CATDrawing).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Full path to the CATIA document to open",
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "catia_save_document",
                "description": "Save the active CATIA document. Optionally save to a new path (Save As).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Optional new file path for Save As",
                        },
                    },
                },
            },
            {
                "name": "catia_close_document",
                "description": "Close the active CATIA document.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "save": {
                            "type": "boolean",
                            "description": "Whether to save before closing (default: false)",
                            "default": False,
                        },
                    },
                },
            },
            {
                "name": "catia_close_all",
                "description": (
                    "Close EVERY open document without saving (fresh session). Use it "
                    "before starting a new piece/assembly: stale documents from earlier "
                    "work (duplicate parts, old copies) make Save As fail and confuse "
                    "which file a product uses. Unsaved work is lost — save first."
                ),
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "catia_list_documents",
                "description": "List all open documents in CATIA V5 with their types and paths.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_get_active_document_info",
                "description": (
                    "Get detailed info about the active CATIA document: "
                    "name, type, path, part bodies, features, etc."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_connect":
                return self.conn.connect()
            case "catia_disconnect":
                return self.conn.disconnect()
            case "catia_new_part":
                return self._new_part(arguments.get("name"))
            case "catia_new_product":
                return self._new_product(arguments.get("name"))
            case "catia_open_document":
                return self._open_document(arguments["file_path"])
            case "catia_save_document":
                return self._save_document(arguments.get("file_path"))
            case "catia_close_document":
                return self._close_document(arguments.get("save", False))
            case "catia_close_all":
                return self._close_all()
            case "catia_list_documents":
                return self._list_documents()
            case "catia_get_active_document_info":
                return self._get_active_document_info()
            case _:
                raise ValueError(f"Unknown document tool: {tool_name}")

    def _new_part(self, name: str | None = None) -> str:
        self.conn.ensure_connected()
        docs = self.conn.documents
        doc = docs.Add("Part")
        rename_note = ""
        if name:
            # Part.Name est en lecture seule (« La méthode Name a échoué », prouvé R19) :
            # le nom d'une pièce = sa référence produit (PartNumber), reprise par Save As.
            try:
                doc.Product.PartNumber = name
            except Exception as e:
                rename_note = f" (PartNumber '{name}' refused: {e})"
        part_name = doc.Product.PartNumber
        self.conn.refresh_display()
        return f"Created new Part document: '{part_name}'{rename_note}"

    def _new_product(self, name: str | None = None) -> str:
        self.conn.ensure_connected()
        docs = self.conn.documents
        doc = docs.Add("Product")
        rename_note = ""
        if name:
            try:
                doc.Product.PartNumber = name
            except Exception as e:
                rename_note = f" (PartNumber '{name}' refused: {e})"
        product_name = doc.Product.PartNumber
        self.conn.refresh_display()
        return f"Created new Product (assembly) document: '{product_name}'{rename_note}"

    def _open_document(self, file_path: str) -> str:
        self.conn.ensure_connected()
        docs = self.conn.documents
        doc = docs.Open(file_path)
        return f"Opened document: '{doc.Name}' from {file_path}"

    def _save_document(self, file_path: str | None = None) -> str:
        import os
        import time

        doc = self.conn.active_document
        app = self.conn.app
        target = os.path.normcase(os.path.abspath(file_path)) if file_path else None
        if target:
            os.makedirs(os.path.dirname(target), exist_ok=True)
        started = time.time()
        alerts = app.DisplayFileAlerts
        app.DisplayFileAlerts = False  # no modal "overwrite?" dialog blocking COM
        error = None
        try:
            doc.SaveAs(file_path) if file_path else doc.Save()
        except Exception as e:  # judged below: R19 raises although it wrote the file
            error = e
        finally:
            app.DisplayFileAlerts = alerts
        written = doc.FullName
        # Proven live (part AND product docs): SaveAs can raise "La méthode SaveAs a
        # échoué" while the file IS written and the document now points at it.
        ok = os.path.isfile(written) and os.path.getmtime(written) >= started - 2 and (
            target is None or os.path.normcase(os.path.abspath(written)) == target
        )
        if not ok:
            raise RuntimeError(f"Save failed ({error}); document still at '{written}'.")
        note = " (CATIA raised a spurious error, but the file is written)" if error else ""
        return f"Document saved: {written} ({os.path.getsize(written) // 1024} KB){note}"

    def _close_all(self) -> str:
        """One bounded pass: products first (a part used by an open product
        cannot be closed — Close() then silently does nothing, which made an
        unbounded 'while documents remain' loop spin forever, seen live)."""
        self.conn.ensure_connected()
        app = self.conn.app
        docs = [app.Documents.Item(i) for i in range(1, app.Documents.Count + 1)]
        docs.sort(key=lambda d: not d.Name.endswith(".CATProduct"))
        closed = 0
        for d in docs:
            try:
                d.Close()
                closed += 1
            except Exception:
                pass  # already closed together with its product
        self.conn.active_body_name = None
        left = [app.Documents.Item(i).Name for i in range(1, app.Documents.Count + 1)]
        return f"Closed the session ({len(docs)} document(s)). Still open: {left or 'none'}"

    def _close_document(self, save: bool = False) -> str:
        doc = self.conn.active_document
        name = doc.Name
        if save:
            doc.Save()
        doc.Close()
        return f"Document '{name}' closed" + (" (saved)" if save else "")

    def _list_documents(self) -> str:
        self.conn.ensure_connected()
        docs = self.conn.documents
        result = []
        for i in range(1, docs.Count + 1):
            doc = docs.Item(i)
            doc_info = {
                "name": doc.Name,
                "path": doc.FullName if hasattr(doc, "FullName") else "unsaved",
            }
            # Detect document type
            try:
                _ = doc.Part
                doc_info["type"] = "CATPart"
            except Exception:
                try:
                    _ = doc.Product
                    doc_info["type"] = "CATProduct"
                except Exception:
                    doc_info["type"] = "Other"
            result.append(doc_info)

        if not result:
            return "No documents open in CATIA"
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _get_active_document_info(self) -> str:
        doc = self.conn.active_document
        info: dict[str, Any] = {
            "name": doc.Name,
            "path": doc.FullName if hasattr(doc, "FullName") else "unsaved",
        }

        # Try Part document
        try:
            part = doc.Part
            info["type"] = "CATPart"
            info["part_name"] = part.Name

            # List bodies
            bodies = part.Bodies
            body_list = []
            for i in range(1, bodies.Count + 1):
                body = bodies.Item(i)
                shapes = []
                for j in range(1, body.Shapes.Count + 1):
                    shapes.append(body.Shapes.Item(j).Name)
                body_list.append({
                    "name": body.Name,
                    "features": shapes,
                })
            info["bodies"] = body_list

            # List geometrical sets (hybrid bodies)
            try:
                hbs = part.HybridBodies
                geo_sets = []
                for i in range(1, hbs.Count + 1):
                    geo_sets.append(hbs.Item(i).Name)
                info["geometrical_sets"] = geo_sets
            except Exception:
                pass

            # Parameters count
            try:
                info["parameters_count"] = part.Parameters.Count
            except Exception:
                pass

            return json.dumps(info, indent=2, ensure_ascii=False)
        except Exception:
            pass

        # Try Product document
        try:
            product = doc.Product
            info["type"] = "CATProduct"
            info["product_name"] = product.Name
            info["part_number"] = product.PartNumber

            # List sub-products
            prods = product.Products
            children = []
            for i in range(1, prods.Count + 1):
                child = prods.Item(i)
                children.append({
                    "name": child.Name,
                    "part_number": child.PartNumber,
                })
            info["components"] = children
            return json.dumps(info, indent=2, ensure_ascii=False)
        except Exception:
            pass

        info["type"] = "Unknown"
        return json.dumps(info, indent=2, ensure_ascii=False)
