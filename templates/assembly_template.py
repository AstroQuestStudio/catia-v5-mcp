r"""Assembly template: copy this file, fill the `# FILL:` markers, run it.

    python assembly_template.py --dry-run     # validate every step against the tool schemas (no CATIA)
    python assembly_template.py               # build in CATIA, solve, clash analysis, save everything
    python assembly_template.py --json        # only write out/<Assembly>/_build/<Assembly>.json

The sample is a 3-part assembly (housing, shaft, bolt). Method, in this order:
  add all parts -> fix the reference part -> PRE-POSITION every other part with move() ->
  constrain with contact / coincidence on real faces and axes (named constraints) -> finish().
Every point is given in the OWN coordinates of its part (part frame, mm):
  axis(x, y, z)  a point ON a cylindrical face, exactly at its radius (designates its axis)
  face(x, y, z)  a point strictly INSIDE a planar face (not on its border, not over a hole)
Get them from the part scripts (they state their frame and interfaces) or with catia_list_faces
on a SMALL part. A point 0.01 mm off the surface fails.
"""
import os
import sys
from pathlib import Path

from catia_mcp.scripting import AssemblyScript, axis, cli, face

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("CATIA_OUT_DIR", HERE / "out"))
# FILL: folder holding the CATPart files produced by the part scripts (they are copied, never modified).
PARTS = Path(os.environ.get("CATIA_PARTS_DIR", HERE / "parts"))

# FILL: assembly name = file name = part number (letters, digits, '_').
NAME = "Gearbox_Sample"

a = AssemblyScript(NAME, OUT / NAME)     # strict_paths=True refuses parts that do not exist yet

# 1. Components. add() returns the instance name CATIA will use: "<PartNumber>.<k>".
#    Purchased parts often have a PartNumber different from the file name: pass part_number=...
housing = a.add(PARTS / "Housing.CATPart")   # FILL: your parts
shaft = a.add(PARTS / "Shaft.CATPart")
bolt = a.add(PARTS / "Bolt.CATPart")

# 2. Reference part: the biggest one, fixed where it stands.
a.fix(housing, "Fix_Housing")

# 3. Pre-position each other part at its final place (mm, degrees). The DSL replays
#    catia_move_component exactly and, after solving, checks nobody was moved by the solver.
#    Rotation turns the part about ITS OWN origin, translation is in world coordinates.
a.move(shaft, tz=5)                      # FILL: translation/rotation computed from the part frames
a.move(bolt, rx=180)                     # e.g. bolt turned head down ...
a.move(bolt, tx=30, ty=0, tz=25)         # ... then carried to the hole

# 4. Constraints. Prefer contact (opposite normals) and coincidence of real faces/axes.
#    Coaxial: axis + axis.   Touching: face + face.   Never mix an axis with a face.
a.coincidence(shaft, axis(10, 0, 20), housing, axis(10, 0, 30), "Coax_Shaft_Housing")      # FILL
a.contact(shaft, face(0, 0, 5), housing, face(20, 0, 5), "Contact_Shaft_Shoulder")          # FILL
a.coincidence(bolt, axis(4, 0, 10), housing, axis(4, 0, 12), "Coax_Bolt_Housing")           # FILL
a.contact(bolt, face(6, 0, 0), housing, face(20, 20, 15), "Contact_Bolt_Head_Housing")      # FILL

#    A distance between planes has no reliable sign: only if nothing else works, and always
#    with an explicit orientation ("same" or "opposite" normals):
# a.offset(cover, face(...), housing, face(...), 5, "Dist_Cover_Housing_5", orientation="opposite")

# 5. Ending: solve, constraint status, poses, clash analysis, save everything, screenshots.
#    allow_clashes lists pairs that interpenetrate ON PURPOSE (press fit); anything else fails the run.
a.finish(views=("isometric", "isometric_back"), allow_clashes=[])   # FILL: views, allowed press fits

if __name__ == "__main__":
    sys.exit(cli(a))
