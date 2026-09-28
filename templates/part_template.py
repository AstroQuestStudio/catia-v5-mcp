r"""Part template: copy this file next to the drawing, fill the `# FILL:` markers, run it.

    python part_template.py --dry-run     # validate every step against the tool schemas (no CATIA)
    python part_template.py               # build in CATIA, save the CATPart, run the built-in checks
    python part_template.py --json        # only write out/<Part>/_build/<Part>.json

The sample below is a flange (disc + hub, bore, bolt circle) so the file runs as it is.
Rules the DSL enforces for you: explicit names (no Pad.1), one sketch per feature, closed
non-crossing profiles, half profiles for revolutions, positive mm values.
Frame: origin at the centre of the flange, axis of the flange = Z, base face on the XY plane.
"""
import math
import os
import sys
from pathlib import Path

from catia_mcp.scripting import PartScript, cli

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("CATIA_OUT_DIR", HERE / "out"))

# FILL: part name = file name = part number (letters, digits, '_'). No spaces, no accents.
NAME = "Flange"

# FILL: dimensions read from the drawing, in millimetres. Measure any dimension that is not written.
OD, T = 80.0, 10.0                       # disc: outer diameter, thickness
HUB_D, HUB_H = 44.0, 12.0                # hub on the +Z face: diameter, height
BORE_D = 30.0                            # through bore
BOLT_PCD, BOLT_D, N_BOLTS = 60.0, 6.0, 6  # bolt circle: diameter, hole diameter, count

p = PartScript(NAME, OUT / NAME)         # density=7850 kg/m3 (steel); change it for other materials

# 1. Base shape goes in the result body (<Part>_Resultat), which PartScript already created.
with p.sketch("xy", "Sk_Disc") as sk:    # FILL: base profile (circle, rect, poly, profile with arcs)
    sk.circle(0, 0, OD / 2)
p.pad(f"Disc_D{OD:g}_T{T:g}", T)

# 2. One body per functional zone, merged into the result with combine().
p.body("Body_Hub")                       # FILL: zone name
with p.sketch(p.plane("xy", offset=T), "Sk_Hub") as sk:   # plane offset = distance along the normal
    sk.circle(0, 0, HUB_D / 2)
p.pad(f"Hub_D{HUB_D:g}_H{HUB_H:g}", HUB_H)
p.combine(p.result, "Body_Hub", "assemble", "Add_Hub")

# 3. Material to remove: build it as solid in its own body, then combine(..., "remove", ...).
#    Cutters are longer than the part (symmetric pad through the origin plane): no coplanar skin left.
p.body("Body_Bore")
with p.sketch("xy", "Sk_Bore") as sk:
    sk.circle(0, 0, BORE_D / 2)
p.pad(f"Bore_Cyl_D{BORE_D:g}", 2 * (T + HUB_H), symmetric=True)
p.combine(p.result, "Body_Bore", "remove", f"Remove_Bore_D{BORE_D:g}")

p.body("Body_Bolt_Holes")
with p.sketch("xy", "Sk_Bolt_Holes") as sk:
    for k in range(N_BOLTS):
        a = 2 * math.pi * k / N_BOLTS
        sk.circle(BOLT_PCD / 2 * math.cos(a), BOLT_PCD / 2 * math.sin(a), BOLT_D / 2)
p.pad(f"Bolt_Cyl_D{BOLT_D:g}", 2 * T, symmetric=True)
p.combine(p.result, "Body_Bolt_Holes", "remove", "Remove_Bolt_Holes")

# 4. Other features you may need (each one named, each one needs its own sketch when it has one):
#    p.hole("Hole_D8", (x, y, z_on_entry_face), 8, depth=12)          # point ON the entry face
#    p.fillet("Round_R2", 2, [(x, y, z)])                             # a point ON each edge
#    p.chamfer("Chamfer_1x45", 1, [(x, y, z)])
#    p.pattern("circ", "Holes_x6", "Hole_D8", count=6, axis_plane="xy")
#    with p.sketch("xy", "Sk_Profile") as sk: sk.profile((0, 0), [line_seg((10, 0)), ...])
#    p.shaft("Turned_Body", "h")                                      # half profile on ONE side of the axis

# 5. Save (measures volume/inertia/bounding box, audits names, writes <Part>.CATPart + screenshots).
p.save()

# 6. What the drawing says, computed BY HAND. The run compares it with CATIA's measurements.
vol = (math.pi / 4) * (OD**2 * T + HUB_D**2 * HUB_H - BORE_D**2 * (T + HUB_H) - N_BOLTS * BOLT_D**2 * T)
p.checks(volume=vol, bbox=(OD, OD, T + HUB_H))   # FILL: expected volume (mm3) and overall size (dx, dy, dz)

if __name__ == "__main__":
    sys.exit(cli(p))
