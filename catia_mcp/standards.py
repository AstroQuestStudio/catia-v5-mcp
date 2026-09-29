"""Technical-drawing standards data and validators (ISO), pure stdlib, no COM.

This module holds *data* (sheet sizes, scales, line widths, general tolerances, thread
pitches, title-block fields) and *checks* for a drawing specification. It never talks to
CATIA and never imports anything outside the standard library, so it is fully testable
offline and can be used both to plan a drawing and to audit one afterwards.

Every table carries the standard it comes from (number and year). Values that could not be
confirmed against the standard text or a second source are marked ``# unverified``.
See ``docs/ISO_STANDARDS.md`` for the scope and the status of each standard.

Conventions
-----------
* Lengths are millimetres. Sheet coordinates: origin at the lower-left corner of the
  *trimmed* sheet, x to the right, y up (CATIA drawings use the same orientation).
* A scale is written like ISO 5455: ``2:1`` enlarges, ``1:2`` reduces. Numerically the
  ``ratio`` is paper length / model length (``2:1`` -> 2.0, ``1:2`` -> 0.5).

Public API
----------
``choose_sheet``, ``preferred_scale``, ``parse_scale``, ``general_tolerance``,
``validate_drawing_spec``, ``thread_designation`` (plus the data tables and helpers).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable, Sequence

__all__ = [
    "ERROR", "WARNING", "Issue", "Sheet", "SheetChoice", "Scale", "LineType", "TitleBlockField",
    "SHEETS", "SHEET_SIZES", "FRAME_LEFT_MM", "FRAME_OTHER_MM", "TITLE_BLOCK_WIDTH_MM",
    "PREFERRED_SCALES", "LINE_WIDTHS", "LINE_GROUPS", "PREFERRED_LINE_GROUPS", "LINE_TYPES",
    "LINE_USAGE", "TITLE_BLOCK_FIELDS", "GENERAL_TOLERANCE_CLASSES", "PROJECTION_METHODS",
    "COARSE_PITCH", "SELECTED_FINE_PITCH", "ThreadError", "ThreadInfo",
    "expected_view_side", "get_sheet", "frame_rect", "drawing_space", "title_block_rect", "choose_sheet",
    "parse_scale", "preferred_scale", "is_preferred_scale", "general_tolerance",
    "general_tolerance_designation", "line_type_for_usage", "line_pair_for_group",
    "is_standard_line_width", "thread_designation", "thread_info",
    "parse_thread_designation", "validate_drawing_spec",
]

ERROR = "error"
WARNING = "warning"

# Standard references used in Issue.reference (kept in one place so tests can pin them).
REF_5457 = "ISO 5457:1999"
REF_5455 = "ISO 5455:1979"
REF_7200 = "ISO 7200:2004"
REF_128_2 = "ISO 128-2:2020 (formerly ISO 128-20:1996 / ISO 128-24:1999)"
REF_129_1 = "ISO 129-1:2018"
REF_2768_1 = "ISO 2768-1:1989"
REF_5456_2 = "ISO 5456-2:1996"
REF_261 = "ISO 261:1998"


# --------------------------------------------------------------------------------------
# Issues
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Issue:
    """One finding of :func:`validate_drawing_spec`."""

    severity: str          # "error" | "warning"
    code: str              # stable machine-readable id, e.g. "title_block.missing_mandatory"
    message: str           # human-readable, English
    reference: str         # standard number + year (+ clause when known)
    path: str = ""         # where in the spec, e.g. "dimensions[3]"

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "code": self.code, "message": self.message,
                "reference": self.reference, "path": self.path}

    def __str__(self) -> str:
        where = f" @ {self.path}" if self.path else ""
        return f"[{self.severity.upper()}] {self.code}{where}: {self.message} ({self.reference})"


# --------------------------------------------------------------------------------------
# Scales - ISO 5455:1979
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Scale:
    """A drawing scale ``num:den`` (``2:1`` enlargement, ``1:2`` reduction)."""

    num: int
    den: int

    def __post_init__(self) -> None:
        if self.num <= 0 or self.den <= 0:
            raise ValueError(f"scale terms must be positive, got {self.num}:{self.den}")

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.num, self.den)

    @property
    def ratio(self) -> float:
        """Paper length / model length."""
        return self.num / self.den

    @property
    def label(self) -> str:
        return f"{self.num}:{self.den}"

    def __str__(self) -> str:
        return self.label


def _scale_from_fraction(f: Fraction) -> Scale:
    return Scale(f.numerator, f.denominator)


def _preferred_fractions(lo_exp: int, hi_exp: int) -> list[Fraction]:
    out = []
    for k in range(lo_exp, hi_exp + 1):
        for m in (1, 2, 5):
            out.append(Fraction(m) * (Fraction(10) ** k))
    return sorted(out, reverse=True)


# ISO 5455:1979 clause 5.1: enlargement 50:1 20:1 10:1 5:1 2:1, full size 1:1,
# reduction 1:2 1:5 1:10 1:20 1:50 1:100 1:200 1:500 1:1000 1:2000 1:5000 1:10000.
# The note of 5.1 allows extending the range by whole powers of 10.
PREFERRED_SCALES: tuple[Scale, ...] = tuple(
    _scale_from_fraction(f) for f in _preferred_fractions(-4, 1) if Fraction(1, 10000) <= f <= 50
)


def parse_scale(value: Any) -> Scale:
    """Parse ``"1:2"``, ``"2:1"``, ``(1, 2)``, a :class:`Scale` or a ratio number.

    A bare number is the paper/model ratio (0.5 -> 1:2, 2 -> 2:1). Raises ValueError.
    """
    if isinstance(value, Scale):
        return value
    if isinstance(value, bool):
        raise ValueError(f"not a scale: {value!r}")
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*", value)
        if not m:
            raise ValueError(f"not a scale (expected 'a:b'): {value!r}")
        f = Fraction(m.group(1)) / Fraction(m.group(2))
        return _scale_from_fraction(f)
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return _scale_from_fraction(Fraction(value[0]) / Fraction(value[1]))
    if isinstance(value, (int, float, Fraction)):
        if value <= 0:
            raise ValueError(f"scale ratio must be positive, got {value!r}")
        return _scale_from_fraction(Fraction(value).limit_denominator(1_000_000))
    raise ValueError(f"not a scale: {value!r}")


def is_preferred_scale(value: Any, *, extended: bool = False) -> bool:
    """True when the scale is in the ISO 5455 recommended table.

    With ``extended=True`` any whole power of ten multiple of a recommended scale is accepted
    (ISO 5455:1979, note to 5.1).
    """
    f = parse_scale(value).fraction
    if not extended:
        return any(f == s.fraction for s in PREFERRED_SCALES)
    for m in (1, 2, 5):
        r = f / m
        # r must be an exact power of ten
        if r.numerator == 1 or r.denominator == 1:
            n, d = r.numerator, r.denominator
            x = n if d == 1 else d
            while x % 10 == 0:
                x //= 10
            if x == 1:
                return True
    return False


def preferred_scale(ratio: Any, mode: str = "fit", *, extended: bool = False) -> Scale:
    """Map a ratio (paper/model) onto a recommended ISO 5455 scale.

    ``mode``:
      * ``"fit"`` (default): the largest recommended scale whose ratio is <= ``ratio``, i.e. the
        biggest standard scale that still fits what ``ratio`` allows;
      * ``"up"``: the smallest recommended scale whose ratio is >= ``ratio``;
      * ``"nearest"``: closest on a logarithmic scale.
    ``ratio`` may be anything :func:`parse_scale` accepts. Outside the table (larger than 50:1 or
    smaller than 1:10000) a ValueError is raised unless ``extended`` is True.
    """
    if mode not in ("fit", "up", "nearest"):
        raise ValueError(f"mode must be 'fit', 'up' or 'nearest', got {mode!r}")
    target = parse_scale(ratio).fraction
    if extended:
        k = int(math.floor(math.log10(float(target)))) if target > 0 else 0
        candidates = sorted(_preferred_fractions(k - 2, k + 2), reverse=True)
    else:
        candidates = [s.fraction for s in PREFERRED_SCALES]
    if mode == "fit":
        ok = [c for c in candidates if c <= target]
        if not ok:
            raise ValueError(f"ratio {float(target):g} is smaller than every recommended scale")
        return _scale_from_fraction(max(ok))
    if mode == "up":
        ok = [c for c in candidates if c >= target]
        if not ok:
            raise ValueError(f"ratio {float(target):g} is larger than every recommended scale")
        return _scale_from_fraction(min(ok))
    return _scale_from_fraction(
        min(candidates, key=lambda c: abs(math.log(float(c)) - math.log(float(target)))))


# --------------------------------------------------------------------------------------
# Sheets - ISO 5457:1999 (Table 1, 4.1, 4.2, 4.3, 4.4)
# --------------------------------------------------------------------------------------

FRAME_LEFT_MM = 20.0    # ISO 5457:1999 4.2: left border 20 mm incl. frame (filing margin)
FRAME_OTHER_MM = 10.0   # ISO 5457:1999 4.2: all other borders 10 mm
FRAME_LINE_WIDTH_MM = 0.7      # ISO 5457:1999 4.2: frame in continuous 0,7 mm lines
GRID_LINE_WIDTH_MM = 0.35      # ISO 5457:1999 4.4
GRID_FIELD_LENGTH_MM = 50.0    # ISO 5457:1999 4.4
CENTRING_MARK_WIDTH_MM = 0.7   # ISO 5457:1999 4.3 (recommended)
TITLE_BLOCK_WIDTH_MM = 180.0   # ISO 7200:2004 clause 6: total width 180 mm (fits an A4 sheet)


@dataclass(frozen=True)
class Sheet:
    """A preferred ISO-A sheet. ``short``/``long`` are the trimmed sheet sides."""

    name: str
    short: float
    long: float
    untrimmed: tuple[float, float]   # (short, long), ISO 5457 Table 1 (a3, b3)
    grid_fields: tuple[int, int]     # (long side, short side), ISO 5457 Table 2

    @property
    def orientation(self) -> str:
        """Orientation ISO 5457 4.1 prescribes: A0..A3 horizontal, A4 vertical."""
        return "portrait" if self.name == "A4" else "landscape"

    def size(self, orientation: str | None = None) -> tuple[float, float]:
        """(width, height) of the trimmed sheet in the given (default: prescribed) orientation."""
        o = orientation or self.orientation
        if o not in ("landscape", "portrait"):
            raise ValueError(f"orientation must be 'landscape' or 'portrait', got {o!r}")
        return (self.long, self.short) if o == "landscape" else (self.short, self.long)


# ISO 5457:1999 Table 1 (trimmed T, untrimmed U) and Table 2 (number of fields).
SHEETS: dict[str, Sheet] = {
    "A0": Sheet("A0", 841, 1189, (880, 1230), (24, 16)),
    "A1": Sheet("A1", 594, 841, (625, 880), (16, 12)),
    "A2": Sheet("A2", 420, 594, (450, 625), (12, 8)),
    "A3": Sheet("A3", 297, 420, (330, 450), (8, 6)),
    "A4": Sheet("A4", 210, 297, (240, 330), (6, 4)),
}
# Trimmed (short, long) per name, handy for tests and callers.
SHEET_SIZES: dict[str, tuple[float, float]] = {k: (s.short, s.long) for k, s in SHEETS.items()}


def get_sheet(name: str) -> Sheet:
    key = str(name).strip().upper()
    if key not in SHEETS:
        raise ValueError(f"unknown sheet size {name!r}; ISO 5457 preferred sizes are {', '.join(SHEETS)}")
    return SHEETS[key]


def frame_rect(sheet: str | Sheet, orientation: str | None = None) -> tuple[float, float, float, float]:
    """Drawing frame ``(x0, y0, x1, y1)``: 20 mm on the left, 10 mm elsewhere (ISO 5457:1999 4.2)."""
    s = sheet if isinstance(sheet, Sheet) else get_sheet(sheet)
    w, h = s.size(orientation)
    return (FRAME_LEFT_MM, FRAME_OTHER_MM, w - FRAME_OTHER_MM, h - FRAME_OTHER_MM)


def drawing_space(sheet: str | Sheet, orientation: str | None = None) -> tuple[float, float]:
    """(width, height) of the drawing space inside the frame.

    Reproduces ISO 5457:1999 Table 1 (e.g. A0 1159 x 821, A4 portrait 180 x 277).
    """
    x0, y0, x1, y1 = frame_rect(sheet, orientation)
    return (x1 - x0, y1 - y0)


def title_block_rect(sheet: str | Sheet, height_mm: float, orientation: str | None = None
                     ) -> tuple[float, float, float, float]:
    """Title block rectangle in the bottom right corner of the frame (ISO 5457:1999 4.1).

    Width is 180 mm (ISO 7200:2004 clause 6). The height is not fixed by the standard; pass yours.
    """
    x0, y0, x1, _ = frame_rect(sheet, orientation)
    w = min(TITLE_BLOCK_WIDTH_MM, x1 - x0)
    return (x1 - w, y0, x1, y0 + float(height_mm))


@dataclass(frozen=True)
class SheetChoice:
    sheet: Sheet
    scale: Scale
    orientation: str
    fits: bool
    required_mm: tuple[float, float]     # paper size of the view block at that scale
    available_mm: tuple[float, float]    # free drawing space after the title block strip
    cols: int
    rows: int
    notes: tuple[str, ...] = ()


def _grid(n: int) -> tuple[int, int]:
    """(cols, rows) used to arrange ``n`` views: 1 -> 1x1, 2 -> 2x1, 3-4 -> 2x2, 5-6 -> 3x2, ..."""
    if n <= 1:
        return (1, 1)
    if n == 2:
        return (2, 1)
    cols = math.ceil(math.sqrt(n))
    if n > 4:
        cols = math.ceil(n / 2)
    rows = math.ceil(n / cols)
    return (cols, rows)


def _margins4(margins: Any) -> tuple[float, float, float, float]:
    """(left, right, bottom, top) clear space around each view, paper mm."""
    if margins is None:
        return (DEFAULT_VIEW_MARGIN_MM,) * 4  # type: ignore[return-value]
    if isinstance(margins, (int, float)):
        return (float(margins),) * 4  # type: ignore[return-value]
    m = tuple(float(x) for x in margins)
    if len(m) == 2:
        return (m[0], m[0], m[1], m[1])
    if len(m) == 4:
        return m  # type: ignore[return-value]
    raise ValueError("margins must be a number, (horizontal, vertical) or (left, right, bottom, top)")


DEFAULT_VIEW_MARGIN_MM = 15.0        # clear space per side of a view for dimensions (a choice, not ISO)
DEFAULT_TITLE_BLOCK_HEIGHT_MM = 40.0  # conservative strip reserved for the title block (not ISO)


def choose_sheet(width_mm: float, height_mm: float, n_views: int = 1, margins: Any = None, *,
                 view_sizes: Sequence[tuple[float, float]] | None = None,
                 title_block_height_mm: float = DEFAULT_TITLE_BLOCK_HEIGHT_MM,
                 min_scale: Any = "1:2", max_scale: Any = "1:1",
                 sheets: Iterable[str] = ("A4", "A3", "A2", "A1", "A0")) -> SheetChoice:
    """Smallest ISO-A sheet and largest recommended scale that hold the views.

    ``width_mm`` x ``height_mm`` is the size *in the model* of one view's bounding box (the
    same size is used for the ``n_views`` views unless ``view_sizes`` gives each view's own
    ``(w, h)``). ``margins`` is the clear space on each side of every view, in paper mm, kept
    free for dimensions: a number, ``(horizontal, vertical)`` or ``(left, right, bottom, top)``
    (default 15 mm). Views are tiled in a grid (2 -> side by side, 3-4 -> 2x2, ...); the height
    of a title block strip is reserved across the whole width, which is conservative.

    Sheets are tried from small to large; on each one the largest recommended scale <=
    ``max_scale`` that fits is taken. The first sheet whose scale is >= ``min_scale`` wins. If no
    sheet reaches ``min_scale`` the largest sheet with its best scale is returned with
    ``fits=True`` and a note, or ValueError if not even 1:10000 fits. Orientation is the one
    ISO 5457:1999 4.1 prescribes (A0..A3 horizontal, A4 vertical).
    """
    sizes: list[tuple[float, float]]
    if view_sizes is not None:
        sizes = [(float(w), float(h)) for w, h in view_sizes]
    else:
        sizes = [(float(width_mm), float(height_mm))] * max(1, int(n_views))
    if not sizes or any(w <= 0 or h <= 0 for w, h in sizes):
        raise ValueError("view sizes must be positive")
    ml, mr, mb, mt = _margins4(margins)
    cols, rows = _grid(len(sizes))
    min_f = parse_scale(min_scale).fraction
    max_f = parse_scale(max_scale).fraction
    # Column widths / row heights of the tiling (row-major placement).
    col_w = [0.0] * cols
    row_h = [0.0] * rows
    for i, (w, h) in enumerate(sizes):
        c, r = i % cols, i // cols
        col_w[c] = max(col_w[c], w)
        row_h[r] = max(row_h[r], h)
    model_w, model_h = sum(col_w), sum(row_h)
    extra_w = cols * (ml + mr)
    extra_h = rows * (mb + mt)

    best: SheetChoice | None = None
    candidates = [get_sheet(n) for n in sheets]
    candidates.sort(key=lambda s: s.short * s.long)
    for sh in candidates:
        dw, dh = drawing_space(sh)
        avail_w, avail_h = dw, dh - title_block_height_mm
        if avail_w <= extra_w or avail_h <= extra_h:
            continue
        # largest ratio r such that model_w*r + extra_w <= avail_w and same for height
        r_max = min((avail_w - extra_w) / model_w, (avail_h - extra_h) / model_h)
        r_frac = Fraction(r_max).limit_denominator(10**9)
        r_frac = min(r_frac, max_f)
        try:
            sc = preferred_scale(r_frac, "fit")
        except ValueError:
            continue
        choice = SheetChoice(
            sheet=sh, scale=sc, orientation=sh.orientation, fits=True,
            required_mm=(model_w * sc.ratio + extra_w, model_h * sc.ratio + extra_h),
            available_mm=(avail_w, avail_h), cols=cols, rows=rows,
        )
        best = choice
        if sc.fraction >= min_f:
            return choice
    if best is None:
        raise ValueError("the views do not fit any ISO-A sheet, even at 1:10000")
    return SheetChoice(
        sheet=best.sheet, scale=best.scale, orientation=best.orientation, fits=True,
        required_mm=best.required_mm, available_mm=best.available_mm, cols=cols, rows=rows,
        notes=(f"scale {best.scale.label} on {best.sheet.name} is smaller than the requested minimum "
               f"{parse_scale(min_scale).label}; consider details or sections, or an elongated sheet "
               f"(ISO 5457:1999 3.2)",))


# --------------------------------------------------------------------------------------
# Lines - ISO 128-20:1996 / ISO 128-24:1999 (now ISO 128-2:2020)
# --------------------------------------------------------------------------------------

# ISO 128-20:1996 clause 4.2: widths in ratio 1:sqrt(2); wide:narrow = 2:1 (extra wide 4:2:1).
LINE_WIDTHS: tuple[float, ...] = (0.13, 0.18, 0.25, 0.35, 0.5, 0.7, 1.0, 1.4, 2.0)
LINE_WIDTH_SERIES_RATIO = math.sqrt(2)

# ISO 128-24:1999 Table 2: line group -> (wide width for 01.2/02.2/04.2, narrow width for
# 01.1/02.1/04.1/05.1). Preferred groups (footnote a): 0,5 and 0,7.
LINE_GROUPS: dict[float, tuple[float, float]] = {
    0.25: (0.25, 0.13), 0.35: (0.35, 0.18), 0.5: (0.5, 0.25), 0.7: (0.7, 0.35),
    1.0: (1.0, 0.5), 1.4: (1.4, 0.7), 2.0: (2.0, 1.0),
}
PREFERRED_LINE_GROUPS = (0.5, 0.7)


@dataclass(frozen=True)
class LineType:
    code: str
    name: str
    wide: bool


# ISO 128-24:1999 Table 1 / Annex A: only the types used on mechanical drawings.
LINE_TYPES: dict[str, LineType] = {
    "01.1": LineType("01.1", "continuous narrow", False),
    "01.2": LineType("01.2", "continuous wide", True),
    "02.1": LineType("02.1", "dashed narrow", False),
    "02.2": LineType("02.2", "dashed wide", True),
    "04.1": LineType("04.1", "long-dashed dotted narrow", False),
    "04.2": LineType("04.2", "long-dashed dotted wide", True),
    "05.1": LineType("05.1", "long-dashed double-dotted narrow", False),
}

# Usage -> (line type, ISO 128-24:1999 Annex A application number).
LINE_USAGE: dict[str, tuple[str, str]] = {
    "imaginary_intersection": ("01.1", "01.1.1"),
    "dimension_line": ("01.1", "01.1.2"),
    "extension_line": ("01.1", "01.1.3"),
    "leader_line": ("01.1", "01.1.4"),
    "hatching": ("01.1", "01.1.5"),
    "revolved_section_outline": ("01.1", "01.1.6"),
    "short_centre_line": ("01.1", "01.1.7"),
    "thread_root": ("01.1", "01.1.8"),
    "dimension_terminator": ("01.1", "01.1.9"),
    "detail_frame": ("01.1", "01.1.12"),
    "projection_line": ("01.1", "01.1.16"),
    "visible_edge": ("01.2", "01.2.1"),
    "visible_outline": ("01.2", "01.2.2"),
    "thread_crest": ("01.2", "01.2.3"),
    "thread_length_limit": ("01.2", "01.2.4"),
    "section_arrow": ("01.2", "01.2.8"),
    "hidden_edge": ("02.1", "02.1.1"),
    "hidden_outline": ("02.1", "02.1.2"),
    "surface_treatment_zone": ("02.2", "02.2.1"),
    "centre_line": ("04.1", "04.1.1"),
    "symmetry_line": ("04.1", "04.1.2"),
    "pitch_circle": ("04.1", "04.1.3"),
    "limited_area": ("04.2", "04.2.1"),
    "cutting_plane": ("04.2", "04.2.2"),
    "adjacent_part": ("05.1", "05.1.1"),
    "extreme_position": ("05.1", "05.1.2"),
    "centroidal_line": ("05.1", "05.1.3"),
    "initial_outline": ("05.1", "05.1.4"),
}


def line_type_for_usage(usage: str) -> str:
    """Line type code (e.g. ``"01.2"``) that ISO 128-24:1999 Annex A prescribes for a usage."""
    try:
        return LINE_USAGE[usage][0]
    except KeyError:
        raise ValueError(f"unknown line usage {usage!r}; known: {', '.join(sorted(LINE_USAGE))}") from None


def line_pair_for_group(group: float) -> tuple[float, float]:
    """(wide, narrow) widths in mm of a line group (ISO 128-24:1999 Table 2)."""
    for g, pair in LINE_GROUPS.items():
        if math.isclose(g, group, abs_tol=1e-9):
            return pair
    raise ValueError(f"unknown line group {group!r}; known: {sorted(LINE_GROUPS)}")


def is_standard_line_width(width_mm: float) -> bool:
    return any(math.isclose(width_mm, w, abs_tol=1e-9) for w in LINE_WIDTHS)


# --------------------------------------------------------------------------------------
# Title block - ISO 7200:2004 (Tables 1-3)
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TitleBlockField:
    key: str
    name: str
    mandatory: bool
    category: str                 # identifying | descriptive | administrative
    max_chars: int | None         # recommended number of characters (None: unspecified)
    clause: str


TITLE_BLOCK_FIELDS: tuple[TitleBlockField, ...] = (
    TitleBlockField("legal_owner", "Legal owner", True, "identifying", None, "5.1.2"),
    TitleBlockField("identification_number", "Identification number", True, "identifying", 16, "5.1.3"),
    TitleBlockField("revision_index", "Revision index", False, "identifying", 2, "5.1.4"),
    TitleBlockField("date_of_issue", "Date of issue", True, "identifying", 10, "5.1.5"),
    TitleBlockField("sheet_number", "Segment/sheet number", True, "identifying", 4, "5.1.6"),
    TitleBlockField("number_of_sheets", "Number of segments/sheets", False, "identifying", 4, "5.1.7"),
    TitleBlockField("language_code", "Language code", False, "identifying", 4, "5.1.8"),
    TitleBlockField("title", "Title", True, "descriptive", 25, "5.2.2"),   # 25 (30 for two-byte scripts)
    TitleBlockField("supplementary_title", "Supplementary title", False, "descriptive", 25, "5.2.3"),
    TitleBlockField("responsible_department", "Responsible department", False, "administrative", 10, "5.3.2"),
    TitleBlockField("technical_reference", "Technical reference", False, "administrative", 20, "5.3.3"),
    TitleBlockField("approval_person", "Approval person", True, "administrative", 20, "5.3.4"),
    TitleBlockField("creator", "Creator", True, "administrative", 20, "5.3.5"),
    TitleBlockField("document_type", "Document type", True, "administrative", 30, "5.3.6"),
    TitleBlockField("classification", "Classification/key words", False, "administrative", None, "5.3.7"),
    TitleBlockField("document_status", "Document status", False, "administrative", 20, "5.3.8"),
    TitleBlockField("page_number", "Page number", False, "administrative", 4, "5.3.9"),
    TitleBlockField("number_of_pages", "Number of pages", False, "administrative", 4, "5.3.10"),
    TitleBlockField("paper_size", "Paper size", False, "administrative", 4, "5.3.11"),
)
_TB_BY_KEY = {f.key: f for f in TITLE_BLOCK_FIELDS}


# --------------------------------------------------------------------------------------
# General tolerances - ISO 2768-1:1989
# --------------------------------------------------------------------------------------

GENERAL_TOLERANCE_CLASSES = ("f", "m", "c", "v")   # fine, medium, coarse, very coarse
_NA = None

# Table 1, linear dimensions: (over, up_to_inclusive, {class: +/- mm}). "over" is exclusive
# except for the first range (0.5 inclusive). None = not applicable in that class.
_LINEAR: tuple[tuple[float, float, dict[str, float | None]], ...] = (
    (0.5, 3, {"f": 0.05, "m": 0.1, "c": 0.2, "v": _NA}),
    (3, 6, {"f": 0.05, "m": 0.1, "c": 0.3, "v": 0.5}),
    (6, 30, {"f": 0.1, "m": 0.2, "c": 0.5, "v": 1.0}),
    (30, 120, {"f": 0.15, "m": 0.3, "c": 0.8, "v": 1.5}),
    (120, 400, {"f": 0.2, "m": 0.5, "c": 1.2, "v": 2.5}),
    (400, 1000, {"f": 0.3, "m": 0.8, "c": 2.0, "v": 4.0}),
    (1000, 2000, {"f": 0.5, "m": 1.2, "c": 3.0, "v": 6.0}),
    (2000, 4000, {"f": _NA, "m": 2.0, "c": 4.0, "v": 8.0}),
)
# Table 2, external radii and chamfer heights (broken edges); f and m share values, c and v too.
_BROKEN_EDGES: tuple[tuple[float, float, dict[str, float | None]], ...] = (
    (0.5, 3, {"f": 0.2, "m": 0.2, "c": 0.4, "v": 0.4}),
    (3, 6, {"f": 0.5, "m": 0.5, "c": 1.0, "v": 1.0}),
    (6, math.inf, {"f": 1.0, "m": 1.0, "c": 2.0, "v": 2.0}),
)
# Table 3, angular dimensions, +/- degrees, by length of the shorter side of the angle (mm).
# unverified: class c for "over 120 up to 400" reads 0 deg 15' in two secondary sources and
# 0 deg 20' in two others; 0 deg 15' (DIN 7168 coarse, same value) is used here.
_ANGULAR: tuple[tuple[float, float, dict[str, float]], ...] = (
    (0, 10, {"f": 1.0, "m": 1.0, "c": 1.5, "v": 3.0}),
    (10, 50, {"f": 0.5, "m": 0.5, "c": 1.0, "v": 2.0}),
    (50, 120, {"f": 20 / 60, "m": 20 / 60, "c": 0.5, "v": 1.0}),
    (120, 400, {"f": 10 / 60, "m": 10 / 60, "c": 15 / 60, "v": 0.5}),
    (400, math.inf, {"f": 5 / 60, "m": 5 / 60, "c": 10 / 60, "v": 20 / 60}),
)


def _norm_class(cls: str) -> str:
    c = str(cls).strip().lower()
    m = re.fullmatch(r"(?:iso\s*2768\s*-?\s*)?([fmcv])", c)
    if not m:
        raise ValueError(f"unknown ISO 2768-1 class {cls!r}; expected one of f, m, c, v")
    return m.group(1)


def general_tolerance(size_mm: float, cls: str = "m", kind: str = "linear") -> float:
    """Permissible deviation (+/-) of ISO 2768-1:1989 for a nominal size.

    ``kind``:
      * ``"linear"`` (Table 1): result in mm, ranges 0.5-4000 mm;
      * ``"broken_edge"`` (Table 2, external radii and chamfer heights): mm;
      * ``"angular"`` (Table 3): result in *degrees*, ``size_mm`` being the length of the shorter
        side of the angle.
    Ranges are "over a, up to and including b" (3 mm belongs to 0.5-3), the first range
    including 0.5. Raises ValueError below 0.5 mm (ISO: indicate the deviation next to the
    dimension), above 4000 mm, and where the standard has no value (class v below 3 mm, class f
    above 2000 mm).
    """
    c = _norm_class(cls)
    size = float(size_mm)
    if kind not in ("linear", "broken_edge", "angular"):
        raise ValueError("kind must be 'linear', 'broken_edge' or 'angular'")
    table: Any = {"linear": _LINEAR, "broken_edge": _BROKEN_EDGES, "angular": _ANGULAR}[kind]
    if kind == "angular":
        if size < 0:
            raise ValueError("angle side length must be >= 0")
        for lo, hi, vals in table:
            if size <= hi:
                return vals[c]
        raise AssertionError("unreachable")
    if size < 0.5:
        raise ValueError("ISO 2768-1 does not cover sizes below 0.5 mm: give the deviation "
                         "next to the nominal size")
    for i, (lo, hi, vals) in enumerate(table):
        if (size > lo or (i == 0 and size >= lo)) and size <= hi:
            v = vals[c]
            if v is None:
                raise ValueError(f"ISO 2768-1 gives no value for class {c!r} at {size:g} mm")
            return v
    raise ValueError(f"{size:g} mm is above the ISO 2768-1 range (4000 mm)")


def general_tolerance_designation(cls: str = "m") -> str:
    """Text to put in or near the title block, e.g. ``"ISO 2768-m"``."""
    return f"ISO 2768-{_norm_class(cls)}"


# --------------------------------------------------------------------------------------
# Projection methods - ISO 5456-2:1996 (also ISO 128-3:2020 clauses 4.5-4.10)
# --------------------------------------------------------------------------------------

PROJECTION_METHODS = ("first_angle", "third_angle")

# Position of each view relative to the front view (A), y up. Verified against ISO 5456-2:1996
# 5.1 and ISO 128-3:2020 4.6 (first angle) and 4.9 (third angle).
_FIRST_ANGLE = {"top": "below", "bottom": "above", "left": "right", "right": "left"}
_THIRD_ANGLE = {"top": "above", "bottom": "below", "left": "left", "right": "right"}
VIEW_KINDS = ("front", "top", "bottom", "left", "right", "rear")


def expected_view_side(projection: str, view: str) -> str | None:
    """Where ``view`` goes relative to the front view: above/below/left/right.

    ``None`` for the front view itself and the rear view (either side, ISO 5456-2:1996 5.1).
    ``view`` is one of top, bottom, left, right ("view from above", "from the left"...).
    """
    if projection not in PROJECTION_METHODS:
        raise ValueError(f"projection must be one of {PROJECTION_METHODS}")
    if view in ("front", "rear"):
        return None
    table = _FIRST_ANGLE if projection == "first_angle" else _THIRD_ANGLE
    if view not in table:
        raise ValueError(f"unknown view kind {view!r}; expected one of {VIEW_KINDS}")
    return table[view]



# --------------------------------------------------------------------------------------
# Metric threads - ISO 261:1998 (general plan), ISO 262 (selected sizes), ISO 724:1993
# --------------------------------------------------------------------------------------

class ThreadError(ValueError):
    """Invalid or non-standard thread designation."""


# Coarse pitch per nominal diameter (mm). Sources: ISO 724 / ISO 261 tables as reproduced by
# secondary references. Sizes tagged in _COARSE_UNCONFIRMED could not be corroborated by two
# independent sources during this work: unverified, confirm against ISO 261:1998 Table 1.
COARSE_PITCH: dict[float, float] = {
    1: 0.25, 1.2: 0.25, 1.4: 0.3, 1.6: 0.35, 1.8: 0.35, 2: 0.4, 2.5: 0.45, 3: 0.5, 3.5: 0.6,
    4: 0.7, 5: 0.8, 6: 1, 7: 1, 8: 1.25, 10: 1.5, 12: 1.75, 14: 2, 16: 2, 18: 2.5, 20: 2.5,
    22: 2.5, 24: 3, 27: 3, 30: 3.5, 33: 3.5, 36: 4, 39: 4, 42: 4.5, 45: 4.5, 48: 5, 52: 5,
    56: 5.5, 60: 5.5, 64: 6,
}
_COARSE_UNCONFIRMED = frozenset({1.4, 1.8, 18, 33, 39, 45, 52, 60})   # unverified

# Fine pitches of the ISO 262 "selected sizes" that were confirmed by a reference table
# (subset; the standard lists more). Other pitches of the ISO 261 series are accepted but
# reported as "not confirmed selected".
SELECTED_FINE_PITCH: dict[float, tuple[float, ...]] = {
    1: (0.2,), 1.2: (0.2,), 1.6: (0.2,), 2: (0.25,), 2.5: (0.35,), 3: (0.35,), 4: (0.5,),
    5: (0.5,), 6: (0.75,), 8: (0.75, 1), 10: (0.75, 1, 1.25), 12: (1, 1.25, 1.5), 16: (1.5,),
    20: (1.5, 2), 24: (2,), 30: (2,), 36: (3,), 42: (3,), 48: (3,), 56: (4,), 64: (4,),
}
# ISO 261 pitch series. unverified: list recalled from ISO 261:1998, not cross-checked.
_PITCH_SERIES = (0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.75, 0.8, 1, 1.25, 1.5, 1.75,
                 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6, 8)

# Basic profile ISO 68-1 (used by ISO 724): H = sqrt(3)/2 P, d2 = d - 0.649519 P, d1 = d - 1.082532 P.
_H_FACTOR = math.sqrt(3) / 2


@dataclass(frozen=True)
class ThreadInfo:
    designation: str        # "M8" (coarse, pitch omitted per ISO 261) or "M8x1"
    nominal: float
    pitch: float
    coarse: bool
    selected_fine: bool     # True only when confirmed in SELECTED_FINE_PITCH
    pitch_diameter: float   # d2 = D2, basic
    minor_diameter: float   # d1 = D1, basic
    height_h: float         # H of the fundamental triangle
    notes: tuple[str, ...] = ()


def _fmt(x: float) -> str:
    return f"{x:g}"


def parse_thread_designation(text: str) -> tuple[float, float | None]:
    """``"M8"`` -> (8, None); ``"M8x1"`` / ``"M8 x 1.25"`` -> (8, 1.0)/(8, 1.25). ThreadError otherwise."""
    m = re.fullmatch(r"\s*M\s*(\d+(?:[.,]\d+)?)\s*(?:[x×X]\s*(\d+(?:[.,]\d+)?))?\s*", str(text))
    if not m:
        raise ThreadError(f"not an ISO metric thread designation: {text!r} (expected M<d> or M<d>x<P>)")
    d = float(m.group(1).replace(",", "."))
    p = float(m.group(2).replace(",", ".")) if m.group(2) else None
    return d, p


def _lookup_nominal(nominal: float) -> float:
    for k in COARSE_PITCH:
        if math.isclose(k, nominal, abs_tol=1e-9):
            return k
    raise ThreadError(f"M{_fmt(nominal)} is not an ISO 261 metric thread size in M1..M64; "
                      f"known sizes: {', '.join('M' + _fmt(k) for k in COARSE_PITCH)}")


def thread_info(nominal: Any, pitch: float | None = None) -> ThreadInfo:
    """Validate a metric thread and return its designation and basic dimensions.

    ``nominal`` is 8, 8.0, ``"8"``, ``"M8"`` or ``"M8x1"`` (a pitch in the text is used when
    ``pitch`` is None). Sizes M1..M64 of ISO 261:1998 only. Coarse pitch is the default and is
    omitted from the designation (``M8``); any other pitch is written ``M8x1``. A pitch larger
    than or equal to a different coarse pitch, or outside the ISO 261 pitch series, raises
    ThreadError. Basic dimensions follow the ISO 68-1 profile used by ISO 724.
    """
    if isinstance(nominal, str):
        s = nominal.strip()
        if s[:1] in ("M", "m") or re.search(r"[x×X]", s):
            d, p_txt = parse_thread_designation(s if s[:1] in ("M", "m") else "M" + s)
            if pitch is None:
                pitch = p_txt
        else:
            try:
                d = float(s.replace(",", "."))
            except ValueError:
                raise ThreadError(f"not a thread size: {nominal!r}") from None
    else:
        d = float(nominal)
    key = _lookup_nominal(d)
    coarse = COARSE_PITCH[key]
    notes: list[str] = []
    if key in _COARSE_UNCONFIRMED:
        notes.append(f"coarse pitch of M{_fmt(key)} not double-checked against ISO 261:1998")  # unverified
    p = coarse if pitch is None else float(pitch)
    if p <= 0:
        raise ThreadError("pitch must be positive")
    is_coarse = math.isclose(p, coarse, abs_tol=1e-9)
    selected = False
    if not is_coarse:
        if not any(math.isclose(p, s, abs_tol=1e-9) for s in _PITCH_SERIES):
            raise ThreadError(f"pitch {_fmt(p)} is not in the ISO 261 pitch series")
        if p > coarse:
            raise ThreadError(f"pitch {_fmt(p)} is coarser than the coarse pitch {_fmt(coarse)} of M{_fmt(key)}")
        selected = any(math.isclose(p, s, abs_tol=1e-9) for s in SELECTED_FINE_PITCH.get(key, ()))
        if not selected:
            notes.append(f"M{_fmt(key)}x{_fmt(p)} is in the ISO 261 pitch series but was not confirmed "
                         f"as an ISO 262 selected size")  # unverified
    designation = f"M{_fmt(key)}" if is_coarse else f"M{_fmt(key)}x{_fmt(p)}"
    return ThreadInfo(
        designation=designation, nominal=key, pitch=p, coarse=is_coarse, selected_fine=selected,
        pitch_diameter=key - 0.649519 * p, minor_diameter=key - 1.082532 * p,
        height_h=_H_FACTOR * p, notes=tuple(notes))


def thread_designation(nominal: Any, pitch: float | None = None) -> str:
    """ISO 261 designation: ``thread_designation(8)`` -> ``"M8"``; ``(8, 1)`` -> ``"M8x1"``.

    The coarse pitch is implied and never written (``thread_designation(8, 1.25) == "M8"``).
    Raises :class:`ThreadError` for sizes or pitches outside ISO 261 (M1..M64).
    """
    return thread_info(nominal, pitch).designation


# --------------------------------------------------------------------------------------
# Drawing specification validator
# --------------------------------------------------------------------------------------

_POS_TOL_MM = 1.0   # alignment tolerance between projected views (a choice, not ISO)


def _add(issues: list[Issue], severity: str, code: str, message: str, reference: str,
         path: str = "") -> None:
    issues.append(Issue(severity, code, message, reference, path))


def _num(x: Any) -> float | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    return None


def _view_box(view: dict[str, Any], scale: Scale | None) -> tuple[float, float, float, float] | None:
    """Paper bounding box (x0, y0, x1, y1) of a view from centre ``position`` and model size."""
    pos = view.get("position")
    w, h = _num(view.get("width_mm")), _num(view.get("height_mm"))
    if not (isinstance(pos, (list, tuple)) and len(pos) == 2 and w and h):
        return None
    cx, cy = _num(pos[0]), _num(pos[1])
    if cx is None or cy is None:
        return None
    try:
        vs = parse_scale(view["scale"]) if view.get("scale") else scale
    except ValueError:
        return None
    if vs is None:
        return None
    return (cx - w * vs.ratio / 2, cy - h * vs.ratio / 2, cx + w * vs.ratio / 2, cy + h * vs.ratio / 2)


def validate_drawing_spec(spec: dict[str, Any]) -> list[Issue]:
    """Check a drawing specification against the retained ISO rules.

    ``spec`` (all keys optional, unknown keys ignored)::

        {
          "sheet": {"size": "A3", "orientation": "landscape"},
          "scale": "1:2",                        # main scale (ISO 5455)
          "projection": "first_angle",           # or "third_angle" (ISO 5456-2)
          "general_tolerance": "m",              # ISO 2768-1 class, or "ISO 2768-m"
          "unit": "mm",
          "views": [{"id": "front", "kind": "front", "position": [x, y],   # centre, sheet mm
                     "width_mm": 80, "height_mm": 40,                      # model size
                     "scale": "1:1"}],                                      # optional own scale
          "lines": [{"usage": "hidden_outline", "type": "02.1", "width_mm": 0.25}],
          "features": [{"id": "h1", "kind": "hole", "diameter_mm": 8}],
          "dimensions": [{"id": "d1", "view": "front", "feature": "h1", "type": "diameter",
                          "value": 8, "unit": "mm", "auxiliary": False, "on_hidden": False,
                          "inside_contour": False, "tolerance": None, "text": "8",
                          "drawn_length_mm": 4.0, "underlined": False,
                          "thread": "M8", "position": [x, y]}],
          "title_block": {"legal_owner": "...", "title": "...", "height_mm": 40}
        }

    Returns a list of :class:`Issue` (severity ``error`` or ``warning``), each with the standard
    (number and year) it comes from. An empty list means no rule was violated.
    """
    issues: list[Issue] = []
    if not isinstance(spec, dict):
        raise TypeError("spec must be a dict")

    # ---- sheet ------------------------------------------------------------------------
    sheet_cfg = spec.get("sheet") or {}
    sheet: Sheet | None = None
    orientation: str | None = None
    if sheet_cfg:
        name = sheet_cfg.get("size") or sheet_cfg.get("format")
        try:
            sheet = get_sheet(name)
        except (ValueError, AttributeError):
            _add(issues, ERROR, "sheet.unknown_size",
                 f"sheet size {name!r} is not an ISO 5457 preferred size (A0..A4)", REF_5457 + " Table 1",
                 "sheet.size")
        if sheet is not None:
            orientation = sheet_cfg.get("orientation") or sheet.orientation
            if orientation not in ("landscape", "portrait"):
                _add(issues, ERROR, "sheet.bad_orientation",
                     f"orientation must be landscape or portrait, got {orientation!r}", REF_5457 + " 4.1",
                     "sheet.orientation")
                orientation = sheet.orientation
            elif orientation != sheet.orientation:
                _add(issues, WARNING, "sheet.orientation",
                     f"ISO 5457 prescribes {sheet.orientation} for {sheet.name} (A0..A3 horizontal, "
                     f"A4 vertical)", REF_5457 + " 4.1", "sheet.orientation")

    # ---- scale ------------------------------------------------------------------------
    main_scale: Scale | None = None
    if "scale" in spec and spec["scale"] is not None:
        try:
            main_scale = parse_scale(spec["scale"])
        except ValueError as e:
            _add(issues, ERROR, "scale.invalid", str(e), REF_5455 + " 3", "scale")
        if main_scale is not None and not is_preferred_scale(main_scale):
            ext = is_preferred_scale(main_scale, extended=True)
            _add(issues, WARNING, "scale.not_preferred",
                 f"scale {main_scale.label} is not in the recommended series"
                 + (" (allowed as a power-of-ten extension)" if ext else
                    " and is not a power-of-ten extension of it (intermediate scales are for "
                    "exceptional cases only)"), REF_5455 + " 5.1", "scale")
    else:
        _add(issues, WARNING, "scale.missing", "no main scale declared", REF_5455 + " 4.1", "scale")

    # ---- projection + views -----------------------------------------------------------
    views = spec.get("views") or []
    projection = spec.get("projection")
    if projection is not None and projection not in PROJECTION_METHODS:
        _add(issues, ERROR, "projection.invalid",
             f"projection must be one of {PROJECTION_METHODS}, got {projection!r}", REF_5456_2, "projection")
        projection = None
    if projection is None and len(views) >= 2:
        _add(issues, ERROR, "projection.missing",
             "several views but no projection method (first or third angle) stated; the symbol must "
             "identify the method", REF_5456_2 + " / ISO 128-3:2020 4.5-4.10", "projection")

    view_ids: dict[str, dict[str, Any]] = {}
    for i, v in enumerate(views):
        vid = v.get("id")
        if vid in view_ids:
            _add(issues, ERROR, "view.duplicate_id", f"view id {vid!r} used twice", REF_5456_2, f"views[{i}].id")
        if vid is not None:
            view_ids[vid] = v
        kind = v.get("kind")
        if kind is not None and kind not in VIEW_KINDS + ("section", "detail", "auxiliary", "isometric"):
            _add(issues, WARNING, "view.unknown_kind", f"unknown view kind {kind!r}", REF_5456_2,
                 f"views[{i}].kind")
        if v.get("scale") and main_scale is not None:
            try:
                vs = parse_scale(v["scale"])
            except ValueError as e:
                _add(issues, ERROR, "scale.invalid", str(e), REF_5455 + " 3", f"views[{i}].scale")
                continue
            if vs != main_scale and not v.get("scale_label"):
                _add(issues, WARNING, "scale.view_label_missing",
                     f"view {vid!r} uses scale {vs.label} different from the main scale "
                     f"{main_scale.label}; it must be written next to the view reference",
                     REF_5455 + " 4.2", f"views[{i}].scale")
            if not is_preferred_scale(vs):
                _add(issues, WARNING, "scale.not_preferred",
                     f"view scale {vs.label} is not in the recommended series", REF_5455 + " 5.1",
                     f"views[{i}].scale")

    # views inside the frame / overlap / arrangement
    boxes: dict[str, tuple[float, float, float, float]] = {}
    tb = spec.get("title_block") or {}
    tb_rect = None
    if sheet is not None:
        tb_h = _num(tb.get("height_mm"))
        if tb_h:
            tb_rect = title_block_rect(sheet, tb_h, orientation)
        fx0, fy0, fx1, fy1 = frame_rect(sheet, orientation)
    for i, v in enumerate(views):
        box = _view_box(v, main_scale)
        if box is None:
            continue
        vid = v.get("id", f"#{i}")
        boxes[vid] = box
        if sheet is not None and (box[0] < fx0 - 1e-6 or box[1] < fy0 - 1e-6
                                  or box[2] > fx1 + 1e-6 or box[3] > fy1 + 1e-6):
            _add(issues, ERROR, "view.outside_frame",
                 f"view {vid!r} extends beyond the drawing frame", REF_5457 + " 4.2", f"views[{i}]")
        if tb_rect and box[0] < tb_rect[2] and box[2] > tb_rect[0] and box[1] < tb_rect[3] and box[3] > tb_rect[1]:
            _add(issues, ERROR, "view.overlaps_title_block",
                 f"view {vid!r} overlaps the title block", REF_5457 + " 4.1", f"views[{i}]")
    ids = list(boxes)
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            A, B = boxes[ids[a]], boxes[ids[b]]
            if A[0] < B[2] and A[2] > B[0] and A[1] < B[3] and A[3] > B[1]:
                _add(issues, ERROR, "view.overlap", f"views {ids[a]!r} and {ids[b]!r} overlap",
                     REF_5456_2 + " 4.2", "views")
    if projection in PROJECTION_METHODS:
        front = next((v for v in views if v.get("kind") == "front"), None)
        fbox = boxes.get(front.get("id")) if front else None
        if fbox:
            fcx, fcy = (fbox[0] + fbox[2]) / 2, (fbox[1] + fbox[3]) / 2
            for i, v in enumerate(views):
                k = v.get("kind")
                if k not in ("top", "bottom", "left", "right"):
                    continue
                box = boxes.get(v.get("id"))
                if not box:
                    continue
                side = expected_view_side(projection, k)
                cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                ok = {"above": cy > fcy, "below": cy < fcy, "left": cx < fcx, "right": cx > fcx}[side]
                if not ok:
                    _add(issues, ERROR, "projection.view_arrangement",
                         f"in {projection.replace('_', ' ')} projection the {k} view goes {side} of "
                         f"the front view", REF_5456_2 + " 5.1 / ISO 128-3:2020 4.6, 4.9", f"views[{i}]")
                    continue
                offs = abs(cx - fcx) if side in ("above", "below") else abs(cy - fcy)
                if offs > _POS_TOL_MM:
                    _add(issues, WARNING, "projection.view_alignment",
                         f"{k} view is not aligned with the front view (offset {offs:.1f} mm)",
                         REF_5456_2 + " 5.1", f"views[{i}]")

    # ---- lines ------------------------------------------------------------------------
    widths_wide: set[float] = set()
    widths_narrow: set[float] = set()
    for i, ln in enumerate(spec.get("lines") or []):
        p = f"lines[{i}]"
        code = ln.get("type")
        usage = ln.get("usage")
        if usage is not None and usage not in LINE_USAGE:
            _add(issues, WARNING, "line.unknown_usage", f"unknown line usage {usage!r}", REF_128_2, p + ".usage")
        elif usage is not None:
            want = LINE_USAGE[usage][0]
            if code is None:
                code = want
            elif code != want:
                _add(issues, ERROR, "line.wrong_type",
                     f"{usage.replace('_', ' ')} must be line type {want} ({LINE_TYPES[want].name}), "
                     f"not {code}", f"ISO 128-24:1999 Annex A {LINE_USAGE[usage][1]} ({REF_128_2})", p + ".type")
        if code is not None and code not in LINE_TYPES:
            _add(issues, WARNING, "line.unknown_type", f"line type {code!r} is not one of the types "
                 f"handled here", REF_128_2, p + ".type")
        w = _num(ln.get("width_mm"))
        if w is not None:
            if not is_standard_line_width(w):
                _add(issues, ERROR, "line.width_not_standard",
                     f"line width {w:g} mm is not in the series {', '.join(f'{x:g}' for x in LINE_WIDTHS)}",
                     REF_128_2 + " (ISO 128-20:1996 4.2)", p + ".width_mm")
            elif code in LINE_TYPES:
                (widths_wide if LINE_TYPES[code].wide else widths_narrow).add(w)
    if widths_wide or widths_narrow:
        if len(widths_wide) > 1 or len(widths_narrow) > 1:
            _add(issues, ERROR, "line.inconsistent_widths",
                 "wide lines must share one width and narrow lines one width (one line group)",
                 "ISO 128-24:1999 Table 2 / ISO 128-20:1996 4.2", "lines")
        elif widths_wide and widths_narrow:
            wide, narrow = next(iter(widths_wide)), next(iter(widths_narrow))
            if not math.isclose(wide / narrow, 2.0, rel_tol=0.08):
                _add(issues, ERROR, "line.ratio",
                     f"wide/narrow width ratio must be 2:1 (got {wide:g}/{narrow:g})",
                     "ISO 128-20:1996 4.2 (wide:narrow = 2:1)", "lines")
            elif not any(math.isclose(wide, g[0]) and math.isclose(narrow, g[1], abs_tol=1e-9)
                         for g in LINE_GROUPS.values()):
                _add(issues, WARNING, "line.group",
                     f"widths {wide:g}/{narrow:g} do not form a standard line group",
                     "ISO 128-24:1999 Table 2", "lines")
        if widths_wide:
            wide = next(iter(widths_wide))
            if not any(math.isclose(wide, g) for g in PREFERRED_LINE_GROUPS):
                _add(issues, WARNING, "line.group_not_preferred",
                     f"line group {wide:g} is not a preferred group (0.5 or 0.7)",
                     "ISO 128-24:1999 Table 2, note a", "lines")

    # ---- general tolerance -----------------------------------------------------------
    gt = spec.get("general_tolerance")
    gt_ok = False
    if gt is not None:
        try:
            _norm_class(gt)
            gt_ok = True
        except ValueError:
            _add(issues, ERROR, "tolerance.class_invalid",
                 f"general tolerance class {gt!r} is not one of f, m, c, v", REF_2768_1, "general_tolerance")

    # ---- dimensions ------------------------------------------------------------------
    dims = spec.get("dimensions") or []
    unit_default = spec.get("unit", "mm")
    seen: dict[tuple[Any, ...], int] = {}
    dimensioned_features: set[Any] = set()
    any_untoleranced = False
    for i, d in enumerate(dims):
        p = f"dimensions[{i}]"
        vid = d.get("view")
        if vid is None or vid not in view_ids:
            _add(issues, ERROR, "dimension.unknown_view",
                 f"dimension {d.get('id', i)!r} refers to view {vid!r} which is not declared",
                 REF_129_1 + " 4.2", p + ".view")
        dtype = d.get("type")
        unit = d.get("unit")
        if dtype == "angular" and not unit:
            _add(issues, ERROR, "dimension.angular_unit_missing",
                 "angular dimensions must always carry their unit", REF_129_1 + " 4.3", p + ".unit")
        if unit and dtype != "angular" and unit != unit_default:
            _add(issues, WARNING, "dimension.mixed_unit",
                 f"dimension in {unit!r} on a drawing whose predominant unit is {unit_default!r}: "
                 f"the unit must be shown on that dimension", REF_129_1 + " 4.3", p + ".unit")
        if d.get("on_hidden"):
            _add(issues, WARNING, "dimension.on_hidden",
                 "dimensioning hidden features is not recommended; use a section or another view",
                 REF_129_1 + " 4.2", p)
        if d.get("inside_contour"):
            _add(issues, WARNING, "dimension.inside_contour",
                 "dimensions should not be placed within the contour of the depicted item",
                 REF_129_1 + " 4.2", p)
        text = d.get("text")
        if isinstance(text, str) and re.search(r"\d\.\d", text):
            _add(issues, WARNING, "dimension.decimal_marker",
                 f"decimal marker in {text!r} should be a comma", REF_129_1 + " 4.1.1", p + ".text")
        if d.get("thread"):
            try:
                info = thread_info(d["thread"])
                if re.search(r"[x×X]", str(d["thread"])) and info.coarse:
                    _add(issues, WARNING, "thread.coarse_pitch_written",
                         f"coarse pitch is implied: write {info.designation}, not {d['thread']!r}",
                         REF_261, p + ".thread")
            except ThreadError as e:
                _add(issues, ERROR, "thread.invalid", str(e), REF_261, p + ".thread")
        # scale consistency of the drawn length (ISO 129-1 4.1.3: out-of-scale values are underlined)
        drawn, val = _num(d.get("drawn_length_mm")), _num(d.get("value"))
        vsc = main_scale
        if vid in view_ids and view_ids[vid].get("scale"):
            try:
                vsc = parse_scale(view_ids[vid]["scale"])
            except ValueError:
                pass
        if drawn is not None and val is not None and vsc is not None and dtype in ("linear", "diameter", "radius"):
            if not math.isclose(drawn, val * vsc.ratio, rel_tol=0.01, abs_tol=0.05) and not d.get("underlined"):
                _add(issues, ERROR, "dimension.out_of_scale",
                     f"drawn length {drawn:g} mm does not match {val:g} mm at {vsc.label} "
                     f"({val * vsc.ratio:g} mm); an out-of-scale value must be underlined",
                     REF_129_1 + " 4.1.3", p)
        pos = d.get("position")
        if sheet is not None and isinstance(pos, (list, tuple)) and len(pos) == 2:
            x, y = _num(pos[0]), _num(pos[1])
            if x is not None and y is not None and not (fx0 <= x <= fx1 and fy0 <= y <= fy1):
                _add(issues, ERROR, "dimension.outside_frame",
                     f"dimension {d.get('id', i)!r} is placed outside the drawing frame",
                     REF_5457 + " 4.2", p + ".position")
        # redundancy
        feat = d.get("feature")
        if feat is not None:
            dimensioned_features.add(feat)
            key = (feat, dtype)
            if not d.get("auxiliary"):
                if key in seen:
                    _add(issues, ERROR, "dimension.redundant",
                         f"feature {feat!r} ({dtype}) is dimensioned more than once; give each dimension "
                         f"once, or mark the repeat as an auxiliary dimension in parentheses",
                         REF_129_1 + " 4.1.1, 4.1.4", p)
                else:
                    seen[key] = i
        if d.get("tolerance") in (None, "") and not d.get("auxiliary") and not d.get("theoretically_exact"):
            any_untoleranced = True
    if any_untoleranced and not gt_ok and gt is None:
        _add(issues, WARNING, "tolerance.general_missing",
             "dimensions without individual tolerance and no general tolerance stated "
             "(e.g. ISO 2768-m in or near the title block)", REF_2768_1 + " / " + REF_129_1 + " 4.1.1",
             "general_tolerance")
    for i, f in enumerate(spec.get("features") or []):
        fid = f.get("id")
        if fid is not None and dims and fid not in dimensioned_features:
            _add(issues, WARNING, "dimension.feature_undimensioned",
                 f"feature {fid!r} has no dimension; only necessary dimensions are shown, but all "
                 f"of them are needed to define the nominal geometry", REF_129_1 + " 4.1.1", f"features[{i}]")

    # ---- title block ------------------------------------------------------------------
    if "title_block" in spec:
        for f in TITLE_BLOCK_FIELDS:
            val = tb.get(f.key)
            empty = val is None or (isinstance(val, str) and not val.strip())
            if f.mandatory and empty:
                _add(issues, ERROR, "title_block.missing_mandatory",
                     f"mandatory title block field '{f.name}' is missing", f"{REF_7200} {f.clause}",
                     f"title_block.{f.key}")
            elif not empty and f.max_chars and len(str(val)) > f.max_chars:
                _add(issues, WARNING, "title_block.too_long",
                     f"'{f.name}' has {len(str(val))} characters, {f.max_chars} recommended",
                     f"{REF_7200} {f.clause}", f"title_block.{f.key}")
        known = set(_TB_BY_KEY) | {"height_mm", "width_mm"}
        for k in tb:
            if k not in known:
                _add(issues, WARNING, "title_block.unknown_field",
                     f"'{k}' is not an ISO 7200 data field (keep it outside the title block or accept a "
                     f"non-conforming block)", REF_7200, f"title_block.{k}")
        wm = _num(tb.get("width_mm"))
        if wm is not None and wm > TITLE_BLOCK_WIDTH_MM + 1e-6:
            _add(issues, WARNING, "title_block.too_wide",
                 f"title block is {wm:g} mm wide; ISO 7200 arrangement is {TITLE_BLOCK_WIDTH_MM:g} mm",
                 REF_7200 + " 6", "title_block.width_mm")
        sn, ns = tb.get("sheet_number"), tb.get("number_of_sheets")
        try:
            if sn is not None and ns is not None and int(sn) > int(ns):
                _add(issues, ERROR, "title_block.sheet_number",
                     f"sheet number {sn} exceeds the number of sheets {ns}", f"{REF_7200} 5.1.6-5.1.7",
                     "title_block.sheet_number")
        except (TypeError, ValueError):
            pass
    elif spec:
        _add(issues, WARNING, "title_block.missing", "no title block in the specification", REF_7200,
             "title_block")

    return issues
