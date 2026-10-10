"""Geometry analysis of one inspection site.

All inputs are klayout shapes already transformed into the reticle field
frame (integer database units). Outputs are plain numbers in nm / um.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from statistics import median

import klayout.db as db

from ..config import Geometry


@dataclass
class Prim:
    """One drawn element reduced to a line-like description (dbu)."""
    width: int
    length: float
    straight: bool
    orient: str | None          # "h" | "v" | None (diagonal / non-manhattan / bent)
    center: tuple[float, float]
    bbox: db.Box


@dataclass
class SiteGeometry:
    kind: str                   # finger_array | single_line | long_line | other | empty
    bbox: db.Box
    cd_dbu: int | None = None
    n_fingers: int = 0
    pitch_dbu: float | None = None
    orientation: str | None = None
    density: str | None = None
    thinnest_dbu: int | None = None
    widths: Counter = field(default_factory=Counter)
    flags: list[str] = field(default_factory=list)


def _orient(dx: float, dy: float) -> str | None:
    if dy == 0 and dx != 0:
        return "h"
    if dx == 0 and dy != 0:
        return "v"
    return None


def _from_path(p: db.Path) -> Prim:
    pts = list(p.each_point())
    length = sum(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(pts, pts[1:]))
    straight = len(pts) == 2
    o = _orient(pts[-1].x - pts[0].x, pts[-1].y - pts[0].y) if straight else None
    bb = p.bbox()
    return Prim(p.width, length, straight, o, (bb.center().x, bb.center().y), bb)


def _from_box(b: db.Box) -> Prim:
    w, h = b.width(), b.height()
    o = "h" if w > h else "v" if h > w else None
    return Prim(min(w, h), max(w, h), True, o, (b.center().x, b.center().y), b)


def min_width(poly: db.Polygon) -> int:
    """Smallest internal width of a polygon (dbu), by bisection on width_check."""
    r = db.Region(poly)
    bb = poly.bbox()
    lo, hi = 1, max(min(bb.width(), bb.height()), 1)
    if r.width_check(hi).is_empty():
        return hi
    # invariant: width_check(lo) empty, width_check(hi) not empty
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if r.width_check(mid).is_empty():
            lo = mid
        else:
            hi = mid
    return lo


def _from_polygon(poly: db.Polygon) -> Prim:
    if poly.is_box():
        return _from_box(poly.bbox())
    bb = poly.bbox()
    w = min_width(poly)
    # line-equivalent length: area / width
    return Prim(w, poly.area() / max(w, 1), False, None, (bb.center().x, bb.center().y), bb)


def shape_to_prim(shape: db.Shape, trans: db.ICplxTrans, ignore_triangles: bool) -> Prim | None:
    if shape.is_path():
        return _from_path(shape.path.transformed(trans))
    if shape.is_box():
        return _from_box(shape.box.transformed(trans))
    if shape.is_polygon() or shape.is_simple_polygon():
        poly = shape.polygon.transformed(trans)
        if ignore_triangles and poly.num_points() == 3:
            return None
        return _from_polygon(poly)
    return None  # texts, edges, points: not geometry


def analyze(prims: list[Prim], dbu_um: float, geo: Geometry) -> SiteGeometry:
    if not prims:
        return SiteGeometry(kind="empty", bbox=db.Box(), flags=["no_geometry"])

    bbox = db.Box()
    for p in prims:
        bbox += p.bbox
    widths = Counter(p.width for p in prims)
    out = SiteGeometry(kind="other", bbox=bbox, widths=widths, thinnest_dbu=min(widths))

    # Finger candidates: straight, manhattan, high aspect ratio. Grouped by
    # (width, orientation); fingers are the narrowest group with >= 2 members.
    lines = [p for p in prims if p.straight and p.orient and p.length >= geo.min_finger_aspect * p.width]
    groups: dict[tuple[int, str], list[Prim]] = {}
    for p in lines:
        groups.setdefault((p.width, p.orient), []).append(p)
    multi = [k for k, v in groups.items() if len(v) >= 2]

    long_dbu = geo.long_line_um / dbu_um
    if multi:
        key = min(multi, key=lambda k: (k[0], -len(groups[k])))
        fingers = groups[key]
        out.kind = "finger_array"
        out.cd_dbu, out.orientation = key
        out.n_fingers = len(fingers)
        axis = 0 if out.orientation == "v" else 1   # fingers along y are spaced in x
        centers = sorted({f.center[axis] for f in fingers})
        if len(centers) < len(fingers):
            out.flags.append("collinear_fingers")
        if len(centers) >= 2:
            diffs = [b - a for a, b in zip(centers, centers[1:])]
            out.pitch_dbu = median(diffs)
            if max(diffs) - min(diffs) > 0.05 * out.pitch_dbu:
                out.flags.append("irregular_pitch")
            space = out.pitch_dbu - out.cd_dbu
            out.density = "isolated" if space >= geo.dense_space_factor * out.cd_dbu else "dense"
        if len(groups) > 1 and any(k[0] < out.cd_dbu for k in groups):
            out.flags.append("narrower_single_line_present")
    else:
        longest = max(prims, key=lambda p: p.length)
        if longest.length >= long_dbu:
            out.kind = "long_line"
        elif lines:
            out.kind = "single_line"
            narrow = min(lines, key=lambda p: p.width)
            out.cd_dbu, out.orientation = narrow.width, narrow.orient
            out.n_fingers = 1
            out.density = "isolated"

    if out.kind in ("other", "long_line") and len(widths) > 1:
        out.flags.append("mixed_widths")
    return out
