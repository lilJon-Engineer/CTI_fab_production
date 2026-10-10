"""`mty list`: show layers, top cells and hierarchy so the config can be filled in."""
from __future__ import annotations

from collections import Counter

import klayout.db as db


def describe(path: str, depth: int = 3, max_children: int = 12) -> str:
    ly = db.Layout()
    ly.read(path)
    out = [f"File: {path}", f"dbu: {ly.dbu} um   cells: {ly.cells()}", "", "Layers (layer/datatype: shapes, flat count not expanded):"]
    for li in ly.layer_indexes():
        n = sum(c.shapes(li).size() for c in ly.each_cell())
        out.append(f"  {ly.get_info(li)!s:12s} {n}")
    out.append("")
    out.append("Top cells:")
    for t in ly.top_cells():
        b = t.dbbox()
        out.append(f"  {t.name}   bbox {b.left:.3f},{b.bottom:.3f} .. {b.right:.3f},{b.top:.3f} um")
    out.append("")
    out.append(f"Hierarchy (depth {depth}; 'xN' = placements, 'empty' = no geometry):")

    def walk(c: db.Cell, d: int) -> None:
        kids = Counter()
        for inst in c.each_inst():
            kids[inst.cell.name] += inst.cell_inst.size()
        names = list(kids)
        for name in names[:max_children]:
            k = ly.cell(name)
            tag = " empty" if k.bbox().empty() else ""
            out.append(f"{'  ' * (d + 1)}{name} x{kids[name]}{tag}")
            if d + 1 < depth:
                walk(k, d + 1)
        if len(names) > max_children:
            out.append(f"{'  ' * (d + 1)}... {len(names) - max_children} more child cells")

    for t in ly.top_cells():
        out.append(f"  {t.name}")
        walk(t, 1)
    return "\n".join(out)
