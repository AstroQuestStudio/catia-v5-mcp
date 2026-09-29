# Lessons learned

Generated from `catia_mcp/data/lessons.json` by `scripts/gen_lessons_doc.py` - do not edit by hand.

Every lesson comes from a mistake actually made and proven on a live CATIA V5 R19 session.
AI agents receive the critical and high ones as server instructions at startup, can query the full
base with the `catia_lessons` tool, and add their own with `catia_add_lesson`.

152 built-in lessons.

## Contents

- [COM automation](#com-automation) (16)
- [Sketcher](#sketcher) (13)
- [Part Design](#part-design) (28)
- [Boolean operations and bodies](#boolean-operations-and-bodies) (2)
- [Topology and references](#topology-and-references) (10)
- [Measurement](#measurement) (7)
- [Assembly Design](#assembly-design) (24)
- [Display and screenshots](#display-and-screenshots) (8)
- [Process and verification](#process-and-verification) (24)
- [Reading drawings](#reading-drawings) (15)
- [Performance](#performance) (5)

## COM automation

### L004 - Set a Parameter object with .Value, never by direct assignment

**Severity:** critical | **Tools:** `catia_shaft`, `catia_groove`, `catia_pad`, `catia_hole`, `catia_set_parameter`

- **Symptom:** Property 'AddNewShaft.FirstAngle' can not be set.
- **Cause:** Angles, lengths and diameters of features are Parameter objects (Angle, Length), not plain numbers.
- **Rule:** Write feature values through '.Value': shaft.FirstAngle.Value = 360, pad.SecondLimit.Dimension.Value = 1, hole.Diameter.Value = 12. Exception: thread Diameter/Pitch/Depth are plain doubles.
- **Matches errors:** `Property '.+' can ?not be set`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L006 - ByRef output arrays come back as zeros from Python

**Severity:** critical | **Tools:** `catia_get_inertia`, `catia_get_bounding_box`, `catia_measure_distance`, `catia_list_components`

- **Symptom:** GetCOG, GetPlane, GetPointsOnCurve, Position.GetComponents, GetAxis return zeros (Area returns 0 on selection references).
- **Cause:** pywin32 late binding does not write back ByRef arrays.
- **Rule:** Run a VBScript inside CATIA: app.SystemService.Evaluate(code, 0, 'CATMain', [args...]). Arguments may be COM objects and Python lists (become VBS arrays); return a numeric array (it comes back as a tuple of floats).
- **Example:** `app.SystemService.Evaluate(vbs, 0, 'CATMain', [measurable_ref, 0.5])`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L001 - Generic E_FAIL carries no cause: isolate by variants, do not guess

**Severity:** high

- **Symptom:** COM raises (-2147352567, 'Une exception s'est produite.', ... 'La méthode UpdateObject a échoué' ..., -2147467259).
- **Cause:** -2147467259 is the generic E_FAIL; the message names the failing method, never the reason.
- **Rule:** Read the message text, not the numeric code. Find the cause by testing variants in a throw-away probe script (one new document per variant), then bake the working variant into the tool.
- **Example:** `(-2147352567, "Une exception s'est produite.", (0, 'CATIAPart', 'La méthode UpdateObject a échoué', None, 0, -2147467259), None)`
- **Matches errors:** `-?2147467259`, `Une exception s.est produite`, `Exception occurred`
- **Proof:** Recurring pattern across every failed feature (shaft, chamfer, hole) during live validation.

### L002 - Feature update failure: UpdateObject failed

**Severity:** high | **Tools:** `catia_shaft`, `catia_groove`, `catia_chamfer`, `catia_pad`, `catia_pocket`, `catia_fillet`

- **Symptom:** 'La méthode UpdateObject a échoué' right after creating a feature.
- **Cause:** The feature definition is invalid (wrong axis, wrong enum, wrong reference, wrong direction) and CATIA cannot rebuild it.
- **Rule:** Do not retry the same call. The server removes the broken feature by itself (see the [cleanup] line; if it says it could not, delete it with catia_delete_feature). Then re-check the feature-specific lesson (shaft/groove axis, chamfer mode, pocket direction, sketch support).
- **Matches errors:** `UpdateObject`, `method UpdateObject failed`
- **Proof:** Seen on shaft without CenterLine, chamfer with mode 0 and '45', sketch on a face of another body.

### L009 - CATIA COM is single-threaded: never parallelise, serialise all calls

**Severity:** high

- **Symptom:** Concurrent calls stall or corrupt CATIA state.
- **Cause:** CATIA's COM server is a single-threaded apartment (STA).
- **Rule:** Issue CATIA calls one at a time. The only safe speed-up is CATIA.RefreshDisplay = False during a tool call, restored in a finally block.
- **Proof:** Design constraint confirmed while optimising tool speed on CATIA V5 R19.

### L011 - A modal dialog blocks all automation: find it, do not click blindly

**Severity:** high | **Tools:** `catia_open_document`, `catia_save_document`, `catia_add_component`

- **Symptom:** Calls hang; no error is returned.
- **Cause:** Any CATIA modal box (open again?, overwrite?, unknown command) blocks the COM call.
- **Rule:** Prevent them (DisplayFileAlerts = False around Open/Save). To locate a modal without a screenshot, enumerate windows of the CNEXT.exe process (win32gui.EnumWindows) and look for class '#32770'. After a hang, do not replay the mutation blindly: re-check session and model state first.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L012 - Never guess a CATIA command name in StartCommand

**Severity:** high

- **Symptom:** A 'Commande inconnue' dialog appears and blocks the COM call.
- **Cause:** StartCommand with an unknown name opens a modal error box. 'Tout développer' does not work through COM either.
- **Rule:** Only start commands whose exact name is verified. To expand the tree use the setting CATCafTreeVizManipSettingCtrl.AutoExpandActivation instead.
- **Matches errors:** `Commande inconnue`, `Unknown command`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L013 - Selection.Item(i).Reference fails from Python on large results

**Severity:** high | **Tools:** `catia_list_faces`, `catia_list_edges`, `catia_fillet`, `catia_chamfer`

- **Symptom:** About 70 % of Selection.Item(i).Reference calls fail on a large part (707 of 894 on a 36-tooth sprocket) while the same calls succeed in VBScript.
- **Cause:** COM marshalling of selection items from Python is unreliable and every round trip is costly.
- **Rule:** Do the whole topological search and reference retrieval inside one VBScript. One bad entry must never abort the loop.
- **Matches errors:** `m.thode Reference a .chou`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L099 - SaveAs raises a generic error although the file is written

**Severity:** high | **Tools:** `catia_save_document`, `catia_save_all`

- **Symptom:** 'La méthode SaveAs a échoué' for a part (or a part inside an assembly), yet the file exists.
- **Cause:** CATIA raises while the file IS written and the document is re-pointed (FullName = target, Saved = True).
- **Rule:** Judge by the result: file exists, modification time after the call, FullName equals the target. Re-raise only if the file really was not written.
- **Matches errors:** `La m.thode SaveAs a .chou`, `method SaveAs failed`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L136 - Read sketch geometry inside CATIA: circle centres need GetCenter

**Severity:** high | **Tools:** `catia_describe_model`

- **Symptom:** Line2D.GetEndPoints / Point2D.GetCoordinates fail from Python, and in VBScript Circle2D.CenterPoint.GetCoordinates silently leaves the centre empty.
- **Cause:** Coordinate outputs are ByRef arrays (zeros or errors from pywin32); the CenterPoint object of a Circle2D or Ellipse2D does not fill them.
- **Rule:** Run one VBScript per sketch (SystemService.Evaluate): Point2D.GetCoordinates, Line2D.StartPoint/EndPoint.GetCoordinates, Circle2D.GetCenter + Radius + GetParamExtents (0..2pi = full circle, otherwise counter-clockwise arc), Ellipse2D.GetCenter/GetMajorAxis. Sketch.GetAbsoluteAxisData gives the frame (origin, H, V) in mm.
- **Matches errors:** `GetEndPoints`, `GetCoordinates`
- **Proof:** Live R19: a sketch with a line, a closed circle, an arc (0.5..2.0 rad) and an ellipse read back exactly through one VBScript; CenterPoint.GetCoordinates returned nothing, GetCenter returned (30, 30).

### L144 - A modal 'files not found or wrong content' dialog blocks every call

**Severity:** high | **Tools:** `catia_list_components`, `catia_add_component`

- **Symptom:** Calls hang; a CATIA dialog 'Open - The following files were not found or do not contain the right information' with buttons Close / Desktop is on screen. Later a COM error such as 'PartNumber failed' appears on a component.
- **Cause:** An assembly points to a file that is missing, renamed or has an over-long path (Windows 260 characters). CATIA asks instead of failing.
- **Rule:** The popup watchdog now presses Close (never Desktop) on this dialog. Then find the broken link: list the components one by one, compare file names before/after the build, and fix the cause (missing file, rename, path length). Never leave the dialog open.
- **Matches errors:** `PartNumber`, `n.ont pas .t. trouv`
- **Proof:** Live R19: a save that suffixed file names past 260 characters left the dialog open for 20 minutes; after Close and the fix the assembly rebuilt cleanly.

### L007 - Return numbers from VBScript, never concatenated strings (locale decimal comma)

**Severity:** medium | **Tools:** `catia_clash_analysis`

- **Symptom:** In VBS, CStr(0.5) gives '0,5' on a French UI, breaking float() parsing.
- **Cause:** VBScript formats numbers with the UI locale.
- **Rule:** Return an array of numbers from SystemService.Evaluate. If you must parse text from CATIA, replace ',' with '.' before float().
- **Example:** `float(value.replace(',', '.'))`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L015 - Feature.Name and Sketch.Name are read/write in raw COM; verify after writing

**Severity:** medium | **Tools:** `catia_rename_feature`, `catia_rename_body`

- **Symptom:** Rename appears to succeed but the name did not change.
- **Cause:** Raw win32com exposes Name as writable, but CATIA may refuse (for instance a duplicate name).
- **Rule:** After setting .Name, re-read it and compare. Names must be unique in the part.
- **Matches errors:** `\[naming\].*(NOT applied|did not take effect|already exists)`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L016 - Part.Name is read-only: use Product.PartNumber

**Severity:** medium | **Tools:** `catia_new_part`, `catia_save_document`

- **Symptom:** 'La méthode Name a échoué' when renaming a part document; renaming via .Name is refused.
- **Cause:** The document name is read-only; the part's name is Document.Product.PartNumber (kept at Save As).
- **Rule:** Set document.Product.PartNumber for the part identity; the file name comes from Save As.
- **Matches errors:** `La m.thode Name a .chou`, `method Name failed`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L017 - Selection.Delete refuses geometry used by another geometry

**Severity:** medium | **Tools:** `catia_delete_feature`

- **Symptom:** 'Interdiction de supprimer une géométrie agrégée par une autre géométrie'.
- **Cause:** The geometry is aggregated (referenced) by another element.
- **Rule:** Delete dependants first, or delete the owning feature instead of the child geometry.
- **Matches errors:** `Interdiction de supprimer`, `agr.g.e par une autre`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L138 - Get the interface name of a COM object from its type info

**Severity:** medium | **Tools:** `catia_describe_model`

- **Symptom:** Shape.Type is missing and the feature kind (Pad, Hole, fillet...) cannot be told from the name, which the user may have changed.
- **Cause:** Late binding hides the class; the tree names are user text in any UI language.
- **Rule:** Use obj._oleobj_.GetTypeInfo().GetDocumentation(-1)[0]: it returns the type library name ('Pad', 'Pocket', 'Shaft', 'Hole', 'ConstRadEdgeFillet', 'Chamfer', 'Assemble', 'Remove', 'Mirror', 'RectPattern', 'CircPattern', 'Body', 'Sketch', 'Line2D', 'PartDocument').
- **Proof:** Live R19 (French UI): every feature of parts built by the scripting kit returned its English interface name whatever its user name.

## Sketcher

### L038 - Shaft/Groove need the revolution axis set as Sketch.CenterLine

**Severity:** critical | **Tools:** `catia_shaft`, `catia_groove`, `catia_sketch_line`

- **Symptom:** catia_shaft or catia_groove fails at update ('La méthode UpdateObject a échoué').
- **Cause:** CATIA reads the revolution axis from Sketch.CenterLine (the Sketcher 'Axis' button). Variants proven to fail: line marked Construction = True, shaft.RevoluteAxis = CreateReferenceFromObject(line), SecondAngle.Value = 0, GeometricElements.Item('AbsoluteAxis').
- **Rule:** Draw the axis with catia_sketch_line(is_axis=true) (or axis='h'/'v' on the feature) BEFORE catia_shaft/catia_groove. In COM: while the sketch is open for edition, sketch.CenterLine = line2D, then AddNewShaft/AddNewGroove(sketch), FirstAngle.Value, part.Update().
- **Matches errors:** `UpdateObject`, `FirstAngle`
- **Proof:** Root cause found live after ruling out FirstAngle, SecondAngle, construction lines and reference objects one by one.

### L043 - An arc sweeping more than 180 degrees is usually a wrong direction

**Severity:** critical | **Tools:** `catia_sketch_profile`

- **Symptom:** A closed profile is accepted but a bulge of material remains (e.g. an R19.5 slot bottom drawn 'ccw' instead of 'cw' went round the wrong side).
- **Cause:** CATIA accepts any valid contour. Changing a coordinate sign without inverting arc directions flips the side of the arc.
- **Rule:** Read every '[check] arc N sweeps ...' line from catia_sketch_profile, compare the point the arc passes through with the drawing, and flip the arc 'direction' if it is on the other side. Inspect the [preview] PNG before creating the feature.
- **Matches errors:** `\[check\]`, `arc \d+ sweeps`, `sweeps \d+.{0,3} \(> ?180`
- **Proof:** A wrong-way arc gave a valid but wrong part, spotted by visual comparison with the drawing.

### L041 - Factory2D has no CreateArc: an arc is CreateCircle with start and end angles

**Severity:** high | **Tools:** `catia_sketch_arc`, `catia_sketch_profile`

- **Symptom:** AttributeError on CreateArc.
- **Cause:** Factory2D.CreateArc does not exist.
- **Rule:** Use Factory2D.CreateCircle(cx, cy, r, start_angle, end_angle) with angles in radians, counter-clockwise.
- **Matches errors:** `CreateArc`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L042 - Separately drawn lines and arcs only join within 1e-4 mm

**Severity:** high | **Tools:** `catia_sketch_profile`, `catia_sketch_two_circle_contour`, `catia_sketch_line`

- **Symptom:** A profile looks closed but the feature fails or produces an open-contour error.
- **Cause:** Independent geometries are not topologically connected.
- **Rule:** For a real closed contour create the vertices with CreatePoint and link them through .StartPoint/.EndPoint (and .CenterPoint for arcs): use catia_sketch_profile or catia_sketch_two_circle_contour.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L044 - A self-intersecting profile is refused by the profile tool

**Severity:** high | **Tools:** `catia_sketch_profile`

- **Symptom:** 'profile crosses itself (segment i crosses segment j near ...). Nothing was drawn.'
- **Cause:** CATIA would accept it and leave an island of material inside the pocket.
- **Rule:** Check each arc's 'direction' and the vertex order; fix the contour then redraw.
- **Matches errors:** `profile crosses itself`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L046 - Sketch edition left open leaves stale results

**Severity:** high | **Tools:** `catia_update_part`, `catia_close_sketch`

- **Symptom:** Sketch changes do not propagate; IsUpToDate is True; Part.Update has no effect.
- **Cause:** An error between OpenEdition and CloseEdition (typically in a VBScript) left the edition open; openings stack.
- **Rule:** Call OpenEdition + CloseEdition again, plus one extra CloseEdition, then UpdateObject(sketch) and part.Update().
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L049 - A sketch cannot be placed on the face of another body

**Severity:** high | **Tools:** `catia_create_sketch`, `catia_hole`

- **Symptom:** The sketch never resolves; Pad/Pocket on it fail; closing the document afterwards crashed CATIA twice.
- **Cause:** CATIA cannot support a sketch on a face belonging to a different body.
- **Rule:** Insert an offset plane coincident with the face into the active body and sketch on it (catia_create_sketch does this automatically). Holes are the exception: they accept a face of another body.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L039 - The axis line is excluded from the profile and need not be a construction line

**Severity:** medium | **Tools:** `catia_sketch_line`, `catia_shaft`, `catia_groove`

- **Symptom:** Unsure how to draw a revolve profile with its axis.
- **Cause:** The CenterLine is taken out of the profile contour.
- **Rule:** Draw the closed profile entirely on one side of the axis. The axis line does not need Construction = True.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L045 - A closed profile must return to its start point

**Severity:** medium | **Tools:** `catia_sketch_profile`

- **Symptom:** 'closed profile does not return to its start: ends at ..., starts at ...'.
- **Cause:** The last vertex differs from the first.
- **Rule:** Add a final segment (its 'to' may be omitted) so the contour closes.
- **Matches errors:** `closed profile does not return to its start`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L047 - Edit an unconstrained sketch with SetData; arc centres are separate elements

**Severity:** medium

- **Symptom:** Moving a circle or point does not move the arcs and lines attached to it.
- **Cause:** Point2D.SetData / Circle2D.SetData only touch that element; an arc's centre point is a separate element; Line2D end points are not re-snapped.
- **Rule:** Move the centre point too, and re-sync Line2D with SetData(x, y, dx, dy). Read coordinates in VBScript (array ByRef). Only valid for sketches without constraints.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L048 - Default sketch axes: xy H=X V=Y, yz H=Y V=Z, zx H=Z V=X

**Severity:** medium | **Tools:** `catia_create_sketch`

- **Symptom:** Profile coordinates come out rotated or mirrored in a sketch on yz or zx.
- **Cause:** The sketch's H and V directions differ per plane.
- **Rule:** Proven defaults (GetAbsoluteAxisData): xy H=X V=Y; yz H=Y V=Z; zx H=Z V=X. To impose a frame use Sketch.SetAbsoluteAxisData((ox,oy,oz, hx,hy,hz, vx,vy,vz)).
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L137 - No sketch degrees-of-freedom analysis in Automation: read constraint statuses

**Severity:** medium | **Tools:** `catia_audit_model`

- **Symptom:** An audit wants to flag under- and over-constrained sketches but the Sketch object has no analysis member.
- **Cause:** The Sketch interface exposes only GeometricElements, Constraints, Factory2D, CenterLine, AbsoluteAxis and edition calls. Contradictory constraints are visible only through Constraint.Status; Constraints.UnUpdatedConstraintsCount also returned 2 on saved sketches that had no constraint, so it is not reliable.
- **Rule:** Report 'no constraint at all' (Constraints.Count = 0) and constraints whose Status is not 0 (1 = not satisfied); never claim a sketch is iso-constrained. Constraint.GetConstraintElement(n).DisplayName gives the constrained element names; add constraints only while the sketch is open (OpenEdition).
- **Proof:** Live R19: member list of Sketch dumped from its type info; two Length constraints of 20 and 30 mm on one line gave Status 1 on both, BrokenConstraintsCount 0 and a failing Part.Update; a horizontality constraint on a line was stored as a Parallelism (type 8) with the reference 'Axe horizontal'.

### L040 - The sketch's absolute axis can serve as revolution axis, only while open for edition

**Severity:** low | **Tools:** `catia_shaft`, `catia_groove`

- **Symptom:** axis='h' or 'v' fails, or GeometricElements.Item('AbsoluteAxis') does not exist.
- **Cause:** The element name 'AbsoluteAxis' does not exist on a localized UI; the Sketch.AbsoluteAxis property works.
- **Rule:** Call sketch.OpenEdition(), assign sketch.CenterLine from sketch.AbsoluteAxis (H or V), then close edition.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

## Part Design

### L053 - Pocket direction is relative to the sketch normal and the default can cut nothing

**Severity:** critical | **Tools:** `catia_pocket`

- **Symptom:** catia_pocket succeeds but the volume does not change (cut goes into empty space).
- **Cause:** DirectionOrientation is relative to the sketch normal (0 along, 1 against) and CATIA defaults to 1: it cuts into material for a sketch on a top face but into air for a sketch on the base plane of an extrusion.
- **Rule:** Measure the body volume before and after; if it did not change, flip the direction (direction='auto' does this). Test for 'changed', not 'decreased': in a body that holds removed volume a pocket makes the measured volume go up.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L061 - Chamfer modes are counter-intuitive: 0 = two lengths, 1 = length and angle

**Severity:** critical | **Tools:** `catia_chamfer`

- **Symptom:** A chamfer '2 x 45' becomes a 45 mm chamfer and the update fails.
- **Cause:** AddNewChamfer(edge, propagation, mode, orientation, L1, L2_or_angle): mode 0 is two lengths, mode 1 is length + angle.
- **Rule:** For length + angle use mode 1 (angle in degrees); mode 0 with 45 makes a 45 mm second length. Propagation 0 = tangency.
- **Matches errors:** `UpdateObject`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L003 - A failed feature left in the tree poisons every later Update

**Severity:** high | **Tools:** `catia_delete_feature`, `catia_shaft`, `catia_groove`, `catia_boolean_operation`

- **Symptom:** After one feature failed, unrelated later part.Update() calls also fail.
- **Cause:** A feature that failed to rebuild stays in the specification tree and makes each following Part.Update() fail.
- **Rule:** Delete the failed feature immediately (catia_delete_feature) before doing anything else. The server does this automatically for Shaft, Groove and boolean operations.
- **Matches errors:** `The failed feature was (removed|deleted)`, `CATIA refused the`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L018 - Part.InWorkObject moves to the last feature, so the active body must be cached

**Severity:** high | **Tools:** `catia_activate_body`, `catia_new_body`, `catia_pad`, `catia_pocket`

- **Symptom:** After the first feature, later sketches and features fall back to the main body.
- **Cause:** Right after 'define in work object' InWorkObject is a Body, but the first created feature moves it to point at that feature.
- **Rule:** Track the active body by name and re-resolve it against Part.Bodies on every call; never derive it from InWorkObject after the first feature. The cached name is lost on server reconnect: call catia_activate_body again.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L052 - Symmetric pad uses the first length on EACH side

**Severity:** high | **Tools:** `catia_pad`

- **Symptom:** A 150 mm symmetric pad measures 300 mm.
- **Cause:** IsSymmetric mirrors the extent, so the first limit is applied on both sides.
- **Rule:** For a total thickness T with symmetric extrusion, set FirstLimit.Dimension.Value = T / 2.
- **Proof:** A 150 lug came out 300 thick.

### L057 - Countersunk hole has NO HeadDiameter: use HeadDepth and HeadAngle

**Severity:** high | **Tools:** `catia_hole`

- **Symptom:** Setting HeadDiameter on a countersunk hole raises an error.
- **Cause:** A countersunk hole is driven by depth and angle.
- **Rule:** Set HeadDepth and HeadAngle; convert a wanted head diameter to the equivalent depth from the angle.
- **Matches errors:** `HeadDiameter`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L062 - Tangency propagation code differs: fillet uses 1, chamfer uses 0

**Severity:** high | **Tools:** `catia_fillet`, `catia_chamfer`

- **Symptom:** Fillet or chamfer follows the wrong edge chain or fails.
- **Cause:** Fillet: AddNewSolidEdgeFilletWithConstantRadius(ref, 1, radius) where 1 = catTangencyFilletEdgePropagation; chamfer propagation 0 = tangency.
- **Rule:** Use propagation 1 for fillets and 0 for chamfers. Add further edges with fillet.AddObjectToFillet(ref).
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L066 - Circular pattern takes 12 arguments; an origin plane as centre and axis rotates around its normal

**Severity:** high | **Tools:** `catia_circ_pattern`

- **Symptom:** AddNewCircPattern with 8 arguments fails or has no axis.
- **Cause:** Signature: AddNewCircPattern(shape, nRadial, nAngular, radialStep, angularStep, radialPos, angularPos, center, axis, reversed, rotation, radiusAligned).
- **Rule:** Pass all 12 arguments. Use an origin plane as both centre and axis to rotate around that plane's normal.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L069 - Repeating an 'up to last' pocket copies the cut shape, not the limit rule

**Severity:** high | **Tools:** `catia_circ_pattern`, `catia_rect_pattern`, `catia_pocket`

- **Symptom:** 7 copies of a radial hole did not go through (the original only cut 1.7 mm in a groove bottom).
- **Cause:** A pattern replicates the shape removed by the original, not its up-to-last limit.
- **Rule:** Use a fixed depth for patterned pockets. Check the volume change announced by the server for each pattern.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L051 - Feature sketches must be re-parented under their feature via InWorkObject

**Severity:** medium | **Tools:** `catia_pad`, `catia_pocket`, `catia_shaft`, `catia_groove`

- **Symptom:** The sketch stays beside the feature at body level instead of under it in the tree.
- **Cause:** AddNewPad/Pocket/Shaft/Groove only absorb the sketch under the feature when Part.InWorkObject = sketch just before (otherwise only the very first feature of a body does).
- **Rule:** Set Part.InWorkObject = sketch immediately before creating the feature; test with sketch.Parent.Name (the feature name, else 'Sketches'). Do it only for the last sketch since the feature is inserted after the work object.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L054 - Limit modes: 0 dimension, 1 up to next, 2 up to last, 3 up to plane

**Severity:** medium | **Tools:** `catia_pocket`, `catia_pad`, `catia_hole`

- **Symptom:** Unsure which LimitMode value to use.
- **Cause:** CATIA limit enum codes.
- **Rule:** FirstLimit.LimitMode: 0 = dimension, 1 = up to next, 2 = up to last. For holes, BottomLimit.LimitMode = 3 (up to plane) with LimitingElement = face reference.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L055 - Hole: AddNewHoleFromPoint(x, y, z, faceRef, depth) and Type 0 simple, 1 tapered, 2 counterbored, 3 countersunk

**Severity:** medium | **Tools:** `catia_hole`

- **Symptom:** Wrong hole type produced.
- **Cause:** Hole type enum codes.
- **Rule:** Use ShapeFactory.AddNewHoleFromPoint(x, y, z, faceRef, depth) then set Type: 0 simple, 1 tapered, 2 counterbored, 3 countersunk.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L056 - Tapered hole angle is HeadAngle; counterbored uses HeadDiameter and HeadDepth

**Severity:** medium | **Tools:** `catia_hole`

- **Symptom:** Taper or counterbore parameters are ignored.
- **Cause:** The taper angle drives HoleTaperedType.1\Angle through HeadAngle.Value.
- **Rule:** Tapered: hole.HeadAngle.Value = angle. Counterbored: HeadDiameter + HeadDepth.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L058 - Hole 'up to plane': BottomLimit.LimitMode = 3 and LimitingElement = face ref

**Severity:** medium | **Tools:** `catia_hole`

- **Symptom:** A hole stops at the wrong depth or fails to reach the target face.
- **Cause:** Up-to-plane needs both the limit mode and the limiting element.
- **Rule:** Set BottomLimit.LimitMode = 3 and BottomLimit.LimitingElement = face reference (a face of another body is accepted).
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L060 - Hole threading mode: 0 = threaded, 1 = smooth

**Severity:** medium | **Tools:** `catia_hole`

- **Symptom:** A threaded hole request produced a smooth hole.
- **Cause:** CatHoleThreadingMode 0 is threaded (tapped) and 1 is smooth; the tool used to set 1.
- **Rule:** Use 0 for a tapped hole, 1 for a smooth one.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L064 - Thread feature: Diameter, Pitch and Depth are plain doubles, not Parameters

**Severity:** medium | **Tools:** `catia_thread`

- **Symptom:** thread.Diameter.Value fails.
- **Cause:** AddNewThreadWithRef(cylinderFace, limitFace) exposes plain doubles.
- **Rule:** Assign thread.Diameter, thread.Pitch, thread.Depth directly (mm). Example: M100x4 over 47 mm: diameter 100, pitch 4, depth 47.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L065 - Thread feature is born a tap: set polarity explicitly

**Severity:** medium | **Tools:** `catia_thread`

- **Symptom:** The thread is created as an internal thread (Taraudage) when an external thread was wanted.
- **Cause:** The new feature is a tap until SetExplicitPolarity is called (CatThreadPolarity catThread = 0, catTap = 1; CatThreadSide right = 0, left = 1).
- **Rule:** Call SetExplicitPolarity(0) for a thread (external) and 1 for a tap; Side 0 is right-hand.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L067 - Circular pattern parameter modes and unequal spacing

**Severity:** medium | **Tools:** `catia_circ_pattern`

- **Symptom:** Unequal angular spacing does not work as expected.
- **Cause:** CircularPatternParameters: 0 instances + spacing, 1 complete crown, 2 unequal spacing (only mode accepting SetInstanceAngularSpacing; index semantics unclear). DesactivatePosition(u, v) numbering is inconsistent ((1,2) removes occurrence 2, (0,2) occurrence 3, (1,0) occurrence 4).
- **Rule:** Do not use DesactivatePosition. For unequal angles create one 2-instance pattern per angle.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L068 - Rectangular pattern takes 12 arguments; directions are origin planes

**Severity:** medium | **Tools:** `catia_rect_pattern`

- **Symptom:** Rectangular pattern goes in the wrong direction or fails.
- **Cause:** Directions are given by origin planes (their normal).
- **Rule:** Pass 12 arguments and origin planes for the two directions.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L071 - Draft signature: AddNewDraft has 10 arguments

**Severity:** medium | **Tools:** `catia_draft`

- **Symptom:** Draft fails or ignores the pull direction.
- **Cause:** AddNewDraft(face, neutral, 0, neutral, dx, dy, dz, 0, angle, 0); extra faces via draft.DraftDomains.Item(1).AddFaceToDraft(ref).
- **Rule:** Pass all 10 arguments with the pulling direction (dx, dy, dz); add other faces to the first domain.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L072 - Mirror mirrors the whole body, not one feature

**Severity:** medium | **Tools:** `catia_mirror`

- **Symptom:** catia_mirror doubled everything.
- **Cause:** AddNewMirror(plane) symmetrises the entire current body.
- **Rule:** Build the feature in its own body if only a subset must be mirrored.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L141 - Edges of a fillet or chamfer can be read back by position, pattern directions cannot

**Severity:** medium | **Tools:** `catia_fillet`, `catia_chamfer`, `catia_describe_model`

- **Symptom:** A fillet or chamfer must be replayed but its edges have no stable name; a rectangular pattern read back gives another direction than the one used to create it.
- **Cause:** ObjectsToFillet / ElementsToChamfer return references that can be measured, whereas RectPattern.GetFirstDirection returns an in-plane vector of the direction plane, not the displacement direction.
- **Rule:** Measure each selected edge with Measurable.GetPointsOnCurve in VBScript and keep its middle point as the designation (catia_fillet edge_points). Do not replay patterns from GetFirstDirection/GetRotationAxis; keep count and spacing as data only.
- **Proof:** Live R19: fillets and a chamfer read back this way were recreated with a volume identical to the original; a pattern created along X read back the direction (0, 1, 0).

### L019 - Body.HybridShapes and Body.Sketches can list foreign or duplicate items

**Severity:** low | **Tools:** `catia_get_tree`, `catia_list_features`

- **Symptom:** A solid feature (e.g. a fillet) shows up under Body.HybridShapes; Body.Sketches of the target body also lists sketches of absorbed bodies.
- **Cause:** CATIA collections overlap after booleans.
- **Rule:** De-duplicate HybridShapes against Body.Shapes, and keep only the body's own sketches when auditing the tree.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L059 - Every hole creates a second internal sketch at update

**Severity:** low | **Tools:** `catia_hole`, `catia_get_tree`

- **Symptom:** An unexpected 'Esquisse.N' / 'Sketch.N' appears after part.Update() for each hole.
- **Cause:** A hole carries two sketches: the positioning point sketch and an internal profile sketch created at update time.
- **Rule:** Rename the two sketches (e.g. Esq_<hole>_Position / _Profile) and hide the internal one so the tree audit passes.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L063 - A fillet on a convex edge at about 120 degrees between an arc and a plane may be refused

**Severity:** low | **Tools:** `catia_fillet`

- **Symptom:** Fillet update fails even with a small radius (R10 on a convex 120 degree edge between an R50 arc face and a planar face).
- **Cause:** Geometrical limitation of the edge configuration.
- **Rule:** Check edge convexity and geometry before insisting; try another edge selection, a chamfer, or model the round in the sketch.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L070 - Shell and thickness signatures

**Severity:** low | **Tools:** `catia_shell`, `catia_thickness`

- **Symptom:** Shell or thickness wrong faces or wrong sides.
- **Cause:** AddNewShell(face, insideThickness, outsideThickness); AddNewThickness(face, offset).
- **Rule:** Use those signatures; volume proves the effect (a shell 8000 -> 3392 mm3 = 8000 - 36 x 16 x 8). Negative thickness offset removes material.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L073 - Groove or pocket can be the first feature of an empty body

**Severity:** low | **Tools:** `catia_groove`, `catia_pocket`, `catia_boolean_operation`

- **Symptom:** Uncertainty about 'machined body' method.
- **Cause:** The body then holds the removed volume, which a boolean removes from the target body.
- **Rule:** This is valid; measured volume of such a body goes UP when a pocket is added.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L139 - Part.IsUpToDate is False on a freshly opened multi-body part

**Severity:** low | **Tools:** `catia_audit_model`, `catia_update_part`

- **Symptom:** After opening a saved part with boolean operations, every feature reports IsUpToDate = False although volume and box are correct.
- **Cause:** The flag means an update is pending, not that the feature failed; the stored geometry is valid.
- **Rule:** Do not treat a False flag as an error by itself: combine it with a body that measures no solid, or with a failing catia_update_part, before calling a feature broken. Part.IsInactive(feature) reports deactivated features separately.
- **Proof:** Live R19: a flange made of a pad and three Assemble/Remove operations reported False for all its features right after Documents.Open, with a volume equal to the value measured before saving.

## Boolean operations and bodies

### L074 - Boolean operations take ONE argument and act in the in-work body

**Severity:** critical | **Tools:** `catia_boolean_operation`

- **Symptom:** AddNewAssemble/Add/Remove/Intersect/UnionTrim(target, tool) fails or applies to the wrong body.
- **Cause:** The tool body is the only argument; the operation is performed in Part.InWorkObject.
- **Rule:** Set the target body as InWorkObject, then call AddNewAssemble(toolBody). Same for AddNewAdd, AddNewRemove, AddNewIntersect, AddNewUnionTrim.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L075 - After a boolean the tool body leaves Part.Bodies

**Severity:** high | **Tools:** `catia_boolean_operation`, `catia_get_tree`, `catia_list_bodies`

- **Symptom:** The operand body cannot be found by name after assemble/add/remove.
- **Cause:** It is absorbed under the boolean feature.
- **Rule:** Access it via the boolean feature's .Body (e.g. Shapes.Item('Assemble_x').Body). Its features appear framed in the tree (normal rendering).
- **Proof:** Verified live on CATIA V5 R19 (French UI).

## Topology and references

### L076 - Topology search must use Topology.CGMFace / CGMEdge / CGMVertex

**Severity:** critical | **Tools:** `catia_list_edges`, `catia_list_faces`, `catia_fillet`

- **Symptom:** 'La méthode Search a échoué' for sel.Search('Topology.Edge,sel') or 'Topology.Face,sel'.
- **Cause:** The spellings Topology.Face and Topology.Edge always fail; CGM types are the valid ones.
- **Rule:** Search 'Topology.CGMFace,sel', 'Topology.CGMEdge,sel' or 'Topology.CGMVertex,sel'.
- **Matches errors:** `La m.thode Search a .chou`, `method Search failed`, `Topology\.(Face|Edge)\b`
- **Proof:** This bug broke edge listing and edge-based fillets from the start of the server.

### L050 - Do not convert raw selection references to BRep names before sketching on a face

**Severity:** high | **Tools:** `catia_create_sketch`, `catia_fillet`, `catia_chamfer`, `catia_hole`

- **Symptom:** Sketches.Add(ref) fails after CreateReferenceFromBRepName(..., WithTemporaryBody / WithPermanentBody).
- **Cause:** The converted reference no longer works as a sketch support.
- **Rule:** Use the raw selection reference (Selection_RSur/REdge:(...)) directly for sketch, fillet, chamfer and hole.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L078 - Search scope: feature versus body, and filter Wire and GSM elements

**Severity:** high | **Tools:** `catia_list_faces`, `catia_list_edges`, `catia_get_bounding_box`

- **Symptom:** A face point returns a sketch edge or an infinite plane; bounding box blows up to +-100 m.
- **Cause:** Searching a Body also returns Wire elements (sketch edges) and GSM elements (inserted planes appear as infinite faces).
- **Rule:** Search on a feature to get its own faces/edges (except the first feature of a body, which equals the result); search the Body for the current result. Exclude DisplayNames containing 'Wire' or 'GSM'.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L079 - Designate geometry by a 3D point, with a temporary point and GetMinimumDistance

**Severity:** high | **Tools:** `catia_fillet`, `catia_chamfer`, `catia_hole`, `catia_create_sketch`

- **Symptom:** Need a face or edge reference without a GUI selection.
- **Cause:** A temporary point HybridShapeFactory.AddNewPointCoord(x, y, z) (not added to the tree, then .Compute) gives a Measurable; GetMinimumDistance to every found face/edge = 0 identifies the element passing through the point.
- **Rule:** Pick a point strictly inside a face (not on an edge, otherwise two faces are at distance 0), coordinates in the part's frame. Do it for all points of a call in one VBScript and stop as soon as all are found.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L081 - A face or axis point must be exact: 0.01 mm off fails

**Severity:** high | **Tools:** `catia_coincidence_constraint`, `catia_contact_constraint`

- **Symptom:** 'closest is 0.010 mm away' when designating a face at 53.99 instead of 54.
- **Cause:** The point must lie exactly on the face; an axis point must be exactly at the cylinder radius, not in a bore or slot.
- **Rule:** Use exact values from catia_list_faces (inside_point, radius), never rounded or measured approximations.
- **Matches errors:** `closest is [\d.,]+ mm away`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L077 - There is no searchable volume topology

**Severity:** medium | **Tools:** `catia_get_inertia`

- **Symptom:** Search with Topology.CGMVolume / CGMSolid fails.
- **Cause:** Those types do not exist.
- **Rule:** Measure a body via its Body reference (volume, area, centre of gravity) instead of searching for a solid.
- **Matches errors:** `CGMVolume|CGMSolid`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L080 - Pierced planar faces have their centre of gravity in the hole

**Severity:** medium | **Tools:** `catia_list_faces`

- **Symptom:** A point at a face's COG is not on the face (flange, washer, rim).
- **Cause:** The COG of a face with a hole lies in the void.
- **Rule:** Use the guaranteed 'inside_point' returned by catia_list_faces (spiral search from the COG in the face plane until distance 0).
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L083 - Raw selection references work for fillet, chamfer, sketch on face and hole

**Severity:** medium | **Tools:** `catia_fillet`, `catia_chamfer`, `catia_hole`, `catia_create_sketch`

- **Symptom:** Unsure which reference form to use.
- **Cause:** Selection_RSur/REdge references are accepted directly by AddNewSolidEdgeFilletWithConstantRadius + AddObjectToFillet, AddNewChamfer, Sketches.Add(ref) and AddNewHoleFromPoint (LimitingElement).
- **Rule:** Pass the raw selection reference as is.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L149 - References to faces created by a Mirror cannot be built from their name

**Severity:** medium | **Tools:** `catia_coincidence_constraint`, `catia_contact_constraint`

- **Symptom:** Designating a face by a point succeeds but CreateReferenceFromName fails on faces that come from a mirrored feature.
- **Cause:** The selection name of a mirrored face is not resolvable as a stand-alone reference.
- **Rule:** Constrain on the original (non-mirrored) faces, or place the second copy with planes and distances instead of designating its mirrored holes.
- **Matches errors:** `CreateReferenceFromName`
- **Proof:** Live R19: holes on the +Y side resolved, the same holes on the -Y side (Mirror) did not.

### L085 - Measurable.GeometryName codes: 4 cylinder, 6 cone, 7 plane

**Severity:** low | **Tools:** `catia_list_faces`

- **Symptom:** Need the surface type of a face.
- **Cause:** GeometryName returns a numeric code.
- **Rule:** 4 = cylinder, 6 = cone, 7 = plane.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

## Measurement

### L088 - Some measurements never work, even in VBScript

**Severity:** high | **Tools:** `catia_measure_distance`

- **Symptom:** GetMinimumDistancePoints returns an empty array; GetMinimumDistance with a Body reference fails both ways; measuring a GSD Extremum computed on a body fails.
- **Cause:** Limitations of CATIA's measure API.
- **Rule:** Do not use these. Measure to faces, edges or points; do not try to measure distance to a Body reference.
- **Matches errors:** `GetMinimumDistance`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L008 - GetWorkbench belongs to the Document, not the Application

**Severity:** medium | **Tools:** `catia_get_inertia`, `catia_measure_distance`

- **Symptom:** app.GetWorkbench('SPAWorkbench') fails.
- **Cause:** SPAWorkbench is obtained from the document.
- **Rule:** Use document.GetWorkbench('SPAWorkbench').
- **Matches errors:** `GetWorkbench`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L086 - Units: area in m2, coordinates in mm

**Severity:** medium | **Tools:** `catia_get_inertia`, `catia_measure_distance`

- **Symptom:** Areas look 1e6 too small.
- **Cause:** SPA workbench returns Area in square metres, GetCOG/GetPlane coordinates in millimetres.
- **Rule:** Convert area from m2 to mm2 (x1e6) before comparing.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L087 - GetCOG fails on an edge; use GetMinimumDistance or GetPointsOnCurve

**Severity:** medium | **Tools:** `catia_measure_distance`

- **Symptom:** GetCOG on an edge reference raises.
- **Cause:** COG only works on surfaces and bodies.
- **Rule:** Use GetMinimumDistance or GetPointsOnCurve for edges.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L089 - Volume, area and centre of gravity: use Measurable on the Body reference

**Severity:** medium | **Tools:** `catia_get_inertia`

- **Symptom:** Volume measurements are slow or wrong.
- **Cause:** Body-level Measurable is the fast and correct source.
- **Rule:** Use catia_get_inertia (Body reference) for volume/area/COG. GetAxis returns a scaled vector: normalise it.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L090 - Exact bounding box is slow: about 0.25 s per face

**Severity:** medium | **Tools:** `catia_get_bounding_box`

- **Symptom:** catia_get_bounding_box takes over a minute on a complex part.
- **Cause:** The only exact method measures the distance of 6 far planes to every face of the result.
- **Rule:** Expect about 0.25 s per face (300 faces = 70 s). Prefer catia_get_inertia unless the exact box is needed.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L142 - Inertia matrix needs Inertias.Add in VBScript and must be removed after

**Severity:** medium | **Tools:** `catia_measure_model`, `catia_get_inertia`

- **Symptom:** Inertia moments read from Python are zeros; the mass looks 1000 times too small or large for the material.
- **Cause:** GetInertiaMatrix/GetPrincipalMoments fill ByRef arrays, and the SPAWorkbench Inertias collection keeps every Inertia you add. Values are in kg.m2 at the part density (1000 kg/m3 when no material is set).
- **Rule:** In VBScript: Set ine = spa.Inertias.Add(body): read GetInertiaMatrix, GetCOGPosition (metres), GetPrincipalMoments, Mass, then spa.Inertias.Remove spa.Inertias.Count. Compare two parts on moments divided by mass when densities may differ.
- **Proof:** Live R19: a bracket of 23616 mm3 gave mass 0.0236 kg at density 1000 and a replay of it reproduced the three principal moments exactly.

## Assembly Design

### L091 - CatConstraintType codes: 0 fix, 1 offset, 2 coincidence, 3 concentricity, 6 angle, 7 planar angle, 20 surface contact

**Severity:** critical | **Tools:** `catia_fix_constraint`, `catia_coincidence_constraint`, `catia_offset_constraint`, `catia_angle_constraint`, `catia_contact_constraint`

- **Symptom:** Constraint created with the wrong meaning (coincidence with 0 = reference/fix, angle with 2, contact with 3 = concentricity).
- **Cause:** Type codes were guessed in the first version. Also: 8 parallelism, 11 perpendicularity, 21 line contact, 22 point contact.
- **Rule:** Use: 0 fix, 1 offset, 2 coincidence, 3 concentricity, 6 angle, 7 planar angle, 20 surface contact. Read codes from the MECMOD type library, never from memory.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L101 - Plane-to-plane offset distance has no reliable sign

**Severity:** critical | **Tools:** `catia_offset_constraint`, `catia_contact_constraint`, `catia_coincidence_constraint`

- **Symptom:** 40 constraints report OK but two wheels are stacked on the same side, nuts sit outside and a bar is flipped.
- **Cause:** The solver accepts either side of an offset; status stays OK.
- **Rule:** Prefer unambiguous constraints: contact (opposite normals) and coincidence of planar faces on real faces. If an offset is unavoidable, pass orientation='opposite' or 'same' and verify the resulting pose, inverting the sign or orientation on mismatch.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L102 - An OK constraint status does not prove the component is in the intended place

**Severity:** critical | **Tools:** `catia_move_component`, `catia_update_assembly`, `catia_list_components`

- **Symptom:** All constraints OK yet a component ended up in another valid solver solution.
- **Cause:** The solver can jump into a wrong but valid solution.
- **Rule:** Pre-position each component exactly (catia_move_component with the computed translation/rotation), then constrain, then compare each component's final pose with the intended pose and fail if the solver moved one.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L092 - Constraint Status codes: 0 OK, 1 not satisfied, 2 orientation, 3 value, 4 wrong geometry type, 5 broken

**Severity:** high | **Tools:** `catia_list_constraints`, `catia_update_assembly`

- **Symptom:** A constraint was created but the assembly is wrong.
- **Cause:** cst.Status reports the solver state.
- **Rule:** Check the status after each constraint: only 0 is OK. 2 = wrong orientation/side, 4 = wrong geometry type (e.g. cylinder face instead of its axis), 5 = broken. Use catia_update_assembly then catia_list_constraints.
- **Matches errors:** `wrong geometry type`, `wrong orientation`, `not satisfied`, `constraint '.*' created, status (?!OK)`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L093 - Cylinder coincidence must target the axis, not the face

**Severity:** high | **Tools:** `catia_coincidence_constraint`

- **Symptom:** Coincidence between two cylinders reports 'wrong geometry type'.
- **Cause:** A cylindrical face reference is the wrong geometry type for a coincidence.
- **Rule:** Reference the axis: "<Root>/<Instance>/!Axis:(<selection name of the cylindrical face>)" (axis_point in the tool).
- **Matches errors:** `wrong geometry type`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L094 - Product-context references use CreateReferenceFromName with a full path

**Severity:** high | **Tools:** `catia_fix_constraint`, `catia_coincidence_constraint`, `catia_contact_constraint`

- **Symptom:** A reference to a component face or plane cannot be resolved.
- **Cause:** Format: "<Root>/<Instance>/!<selection name of the face in its part>"; whole component (fix): "<Root>/<Instance>/!<Root>/<Instance>/"; origin plane: "<...>/!Plan zx" (localized plane name).
- **Rule:** Build references with exactly that syntax; plane names are localized.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L095 - Designating geometry in a product part needs Documents.Open, which loads a 2nd copy

**Severity:** high | **Tools:** `catia_coincidence_constraint`, `catia_contact_constraint`, `catia_offset_constraint`

- **Symptom:** Topological search returns nothing for a part without a window; with alerts on, a modal 'open again?' box blocks; with alerts off a SECOND copy loads (8 documents for 3 components).
- **Cause:** CATIA only searches topology in a document with a window.
- **Rule:** Open with DisplayFileAlerts = False, take the selection name (identical for the instance since topology is the same), then close the extra copy immediately (it is not used by any product); otherwise it makes Save As of the real part fail. Re-activate the product window afterwards.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L097 - A part used by an open product cannot be closed

**Severity:** high | **Tools:** `catia_close_all`, `catia_close_document`

- **Symptom:** Close does nothing (on the document and on its window); a 'while documents remain: close' loop ran forever.
- **Cause:** CATIA keeps parts referenced by open products loaded.
- **Rule:** Close products first, then parts, in ONE pass (catia_close_all). Never write unbounded closing loops.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L098 - SaveAs of the root product alone fails once a part is modified

**Severity:** high | **Tools:** `catia_save_all`

- **Symptom:** 'les données importées ont été modifiées dans la session'.
- **Cause:** Designating geometry marks the parts modified, and the root cannot be saved alone.
- **Rule:** Save ALL documents into the target folder (catia_save_all); original files are never overwritten.
- **Matches errors:** `donn.es import.es ont .t. modifi.es`, `imported data.*modified`
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L145 - Save As on a multi-instance part must not rename it again

**Severity:** high | **Tools:** `catia_save_all`

- **Symptom:** After catia_save_all, a part inserted 4 times exists as several files with chained suffixes and the parent assembly cannot find them when reopened.
- **Cause:** Save As re-points the document: the next instance of the same reference reports the NEW path. Treated as a different document, it was saved again under another name.
- **Rule:** Track saved documents by identity, and mark the new FullName as handled right after Save As. Only two DIFFERENT documents with the same file name get a part-number suffix. Check with a part inserted several times in the same assembly.
- **Proof:** Live R19: a washer inserted 4 times produced 3 chained suffixes (path over 260 characters, dialog on reopen); with the fix one file per document and the assembly reopens.

### L147 - Plane coincidence has an orientation: the default can flip a whole sub-assembly by 180 degrees

**Severity:** high | **Tools:** `catia_coincidence_constraint`

- **Symptom:** Constraints are all OK but the component is turned by 180 degrees (mirrored position, hundreds of millimetres away).
- **Cause:** Coincidence of two planes/axes accepts both normals; the solver keeps the side it finds, not the one you meant.
- **Rule:** Pass orientation explicitly on plane coincidences (same or opposite) and always verify the final pose against the expected one (pose check). An OK status never proves the intended place.
- **Proof:** Live R19: a leg on a fuselage pin came out turned by 180 degrees with all constraints OK; orientation opposite gave the expected pose.

### L148 - Offset between two planes: the sign is not reliable, read the pose back

**Severity:** high | **Tools:** `catia_offset_constraint`

- **Symptom:** A piston ends up 2500 mm from its cylinder although the constraint status is OK.
- **Cause:** The sign of a plane-to-plane distance depends on the normals; the wrong sign is accepted.
- **Rule:** After every offset, compare the resulting pose with the expected one; if it is opposite, flip the sign. Prefer contact and coincidence on real faces when possible.
- **Proof:** Live R19: a retract piston displaced by 2520 mm with status OK, detected by the pose check, fixed by the opposite sign.

### L096 - Components are added with Products.AddNewComponent, not AddNewProduct

**Severity:** medium | **Tools:** `catia_add_new_part`

- **Symptom:** AddNewProduct('Part') creates a Product, not a Part.
- **Cause:** Wrong factory method.
- **Rule:** Use Products.AddNewComponent('Part', partNumber) to create a part inside a product.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L100 - SaveAs to a file already loaded in the session fails

**Severity:** medium | **Tools:** `catia_save_all`, `catia_save_document`, `catia_close_all`

- **Symptom:** SaveAs fails with a generic error when the target file is loaded.
- **Cause:** CATIA refuses to overwrite a document that is open.
- **Rule:** Start from a clean session (catia_close_all) before saving to an already loaded path.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L103 - Fix the reference component first and constrain the rest to it

**Severity:** medium | **Tools:** `catia_fix_constraint`

- **Symptom:** Everything drifts when the assembly updates.
- **Cause:** Nothing anchors the assembly.
- **Rule:** Fix the reference (largest) component with catia_fix_constraint, then add the others with named constraints.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L104 - Do not over-constrain rotation: a redundant constraint creates a conflict

**Severity:** medium | **Tools:** `catia_coincidence_constraint`

- **Symptom:** Constraint status 1/5 after adding a second constraint on the same rotation.
- **Cause:** Free rotation about an axis is fine when it has no functional meaning.
- **Rule:** Leave rotation free (pre-positioning fixes it) unless it matters (aligned bolt holes: add a second coincidence on a hole axis, never a redundant one).
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L107 - Clash analysis: expect zero clashes except deliberate fits

**Severity:** medium | **Tools:** `catia_clash_analysis`

- **Symptom:** Interferences reported between components.
- **Cause:** Press fits, cosmetic threads and toroidal seals modelled at rest legitimately interfere (e.g. 0.5 mm radial press fit).
- **Rule:** Run catia_clash_analysis after assembling. A correct assembly has 0 clash apart from intended interferences, which must be explained in the documentation. The analysis object is removed afterwards.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L108 - Never guess component instance names; list them

**Severity:** medium | **Tools:** `catia_list_components`, `catia_add_component`, `catia_add_sub_assembly`

- **Symptom:** A constraint fails because the instance path is wrong.
- **Cause:** Instance names are '<PartNumber>.<k>' and a sub-assembly's children are addressed 'Sub.1/Piece.1'; purchased parts use their full part number.
- **Rule:** Call catia_list_components and copy the names. Insert a sub-assembly by its CATProduct file.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L110 - Product-level update is required for constraints to move parts

**Severity:** medium | **Tools:** `catia_update_assembly`

- **Symptom:** Constraints exist but parts did not move.
- **Cause:** Constraints only reposition components on update.
- **Rule:** Call catia_update_assembly after adding constraints and check that all constraints are OK.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L111 - Assembly checklist: all constraints OK, no default names, zero unexplained clash

**Severity:** medium | **Tools:** `catia_update_assembly`, `catia_list_constraints`, `catia_clash_analysis`

- **Symptom:** Assembly declared finished but hidden problems remain.
- **Cause:** Assembly quality has three measurable criteria.
- **Rule:** Verify catia_update_assembly says all OK, catia_list_constraints shows no default names, and catia_clash_analysis has 0 clash except justified fits.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L146 - Two catalogue parts with the same file name collide in save_all

**Severity:** medium | **Tools:** `catia_save_all`

- **Symptom:** Save As fails for the second of two different parts that share a file name, coming from two sub-assemblies.
- **Cause:** Both documents map to the same target file in the destination folder.
- **Rule:** Give distinct documents distinct files: load one of them from a renamed copy before building, or let catia_save_all suffix the second one with its part number.
- **Matches errors:** `SaveAs`
- **Proof:** Live R19: the same catalogue file used with two different part numbers in two sub-assemblies made the final save fail until one copy was renamed.

### L105 - Coincidence on axes for coaxial parts, contact for planar seating

**Severity:** low | **Tools:** `catia_coincidence_constraint`, `catia_contact_constraint`

- **Symptom:** Uncertainty on which constraint to use.
- **Cause:** Standard practice proven on assemblies.
- **Rule:** Coaxiality: coincidence of the two axes. Planar seating: surface contact. Distance: offset. Give constraints role-based names (Fixed_, Coax_, Contact_, Dist_).
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L106 - Planar washers or nuts tangent to a curved bore intersect by their corners

**Severity:** low | **Tools:** `catia_clash_analysis`

- **Symptom:** 0.9 mm interference on a nut or washer seated on a bore.
- **Cause:** The corners of a polygon enter a curved surface.
- **Rule:** Offset the component slightly and re-run catia_clash_analysis.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L109 - Repeated fasteners: one instance each, pre-positioned and constrained

**Severity:** low | **Tools:** `catia_duplicate_component`

- **Symptom:** Bolt patterns are slow or wrong with a single constraint.
- **Cause:** Each instance needs its own placement and constraints.
- **Rule:** Create one instance per fastener (catia_duplicate_component), pre-position each, then add coaxiality with its hole and contact under the head.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

## Display and screenshots

### L112 - Capture the CATIA window with PrintWindow, not a screen copy

**Severity:** high | **Tools:** `catia_screenshot`

- **Symptom:** A screenshot shows another window (a game) instead of CATIA.
- **Cause:** BitBlt copies the pixels of whatever window is in front.
- **Rule:** Use PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT) so CATIA draws itself, even when hidden. Find the window through the CNEXT.exe process, not a title containing 'CATIA'. Never take a full-screen capture of the user's desktop.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L010 - A script killed while RefreshDisplay=False leaves CATIA frozen

**Severity:** medium | **Tools:** `catia_connect`

- **Symptom:** The 3D view no longer redraws after an aborted script.
- **Cause:** RefreshDisplay was left False.
- **Rule:** Always restore RefreshDisplay = True in a finally block; on connect, force it back to True.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L113 - A minimised CATIA window captures as a blank image

**Severity:** medium | **Tools:** `catia_screenshot`

- **Symptom:** PrintWindow returns an empty image.
- **Cause:** A minimised window does not render.
- **Rule:** Restore it with SW_SHOWNOACTIVATE and HWND_BOTTOM (no focus steal, stays behind the user's windows) and re-maximise afterwards if it was maximised.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L115 - CATIA is Z-up: standard view vectors

**Severity:** medium | **Tools:** `catia_set_view`

- **Symptom:** Isometric view lays the part on its side.
- **Cause:** A Y-up view table was used.
- **Rule:** Use CATIA's own directions: front sight (0,1,0) up (0,0,1); back (0,-1,0); top sight (0,0,-1) up (0,1,0); bottom (0,0,1) up (0,-1,0); left (1,0,0); right (-1,0,0); isometric sight (-1,-1,-1) up (-1,-1,2). Keep 'up' perpendicular to 'sight'.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L114 - Tree collapsed on screenshots: enable AutoExpandActivation

**Severity:** low | **Tools:** `catia_screenshot`, `catia_connect`

- **Symptom:** The specification tree is folded in captures.
- **Cause:** Tree expansion is a session setting.
- **Rule:** Set SettingControllers.Item('CATCafTreeVizManipSettingCtrl').AutoExpandActivation = True on connect (session only).
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L116 - Clean the display before presentation screenshots

**Severity:** low | **Tools:** `catia_clean_display`, `catia_screenshot`

- **Symptom:** Captures show planes, sketches, axis systems and constraint symbols.
- **Cause:** Everything is visible by default, including in sub-assemblies (green markers stay if only root ones are hidden).
- **Rule:** Use catia_clean_display before screenshots: hide origin/offset planes, sketches, axis systems and constraint symbols including sub-assemblies, and collapse the tree after the first view.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L117 - Do not Reframe after every operation

**Severity:** low | **Tools:** `catia_fit_all`

- **Symptom:** The view jumps and everything is slow.
- **Cause:** Reframe redraws and moves the view.
- **Rule:** Refresh once at the end of each tool call; call catia_fit_all on demand only.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L118 - Offset planes inserted for sketches must be hidden

**Severity:** low | **Tools:** `catia_create_sketch`

- **Symptom:** Coincident offset planes clutter the view and tree.
- **Cause:** They are helper geometry created for sketch-on-other-body.
- **Rule:** Hide them and reuse an existing one instead of creating duplicates.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

## Process and verification

### L005 - Never guess a COM signature or enum value; dump the type library

**Severity:** critical

- **Symptom:** A call fails or silently does the wrong thing (circular pattern with 8 args instead of 12, contact = 3 instead of 20, chamfer modes swapped, thread polarity inverted).
- **Cause:** CATIA's Automation API is large and counter-intuitive; enum values and argument lists cannot be guessed. Almost every early bug came from a guessed signature or enum.
- **Rule:** Extract the exact API (signatures, parameter names, enum values) from the CATIA type libraries into text files and grep them (e.g. 'AddNewChamfer', 'enum CatHoleType') before writing any COM call. Never invent a method, argument count or constant.
- **Proof:** Root cause of nearly all bugs found during the first live validation session.

### L023 - Solid features must announce a volume change; zero or absurd means failure

**Severity:** critical | **Tools:** `catia_pad`, `catia_pocket`, `catia_get_inertia`

- **Symptom:** A feature is created and the update succeeds but nothing changed (or changed the wrong way).
- **Cause:** A clean update does not prove the intended geometry effect (wrong pocket direction, non-through cut, wrong side).
- **Rule:** Measure volume before and after every material-changing feature. Zero variation or a wrong sign or magnitude is an error; fix it before adding more features.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L024 - A plausible volume does not prove the shape: overlay the profile on the drawing

**Severity:** critical | **Tools:** `catia_sketch_profile`

- **Symptom:** A valid, non-self-intersecting profile with a plausible volume was still wrong (an arc drawn round the wrong side left a bulge of material).
- **Cause:** CATIA accepts any valid closed contour; volume checks cannot detect a shape that is merely different.
- **Rule:** For each non-trivial profile, superimpose the contour on the drawing view at high resolution and compare views at the drawing's isometric angle before declaring the part done.
- **Proof:** A wrong-way arc was found by visual review, not by any numeric check.

### L143 - Two agents or scripts driving one CATIA freeze it (and can crash the PC)

**Severity:** critical

- **Symptom:** CATIA stops responding (Responding=False), COM calls hang for minutes, the whole machine may crash.
- **Cause:** CATIA's COM server is single-threaded and heavy calls (designation, update, clash) from two clients interleave; each one also leaves documents and windows the other does not expect.
- **Rule:** Run ONE CATIA client at a time. Chain agents sequentially, or set CATIA_MCP_LOCK=1 so processes queue on the cross-process lock. Give every run a time limit (runner --hang-seconds, timeout) and prefer killing a frozen CNEXT and relaunching over waiting.
- **Proof:** Live R19: two assembly scenarios in parallel froze CATIA (CNEXT not responding for 30+ min); run one after the other the same scenarios finished in 2-6 minutes.

### L014 - Probe unknown COM APIs in a fresh document each, and close it

**Severity:** high

- **Symptom:** CATIA crashed after many open documents combined with repeated failures (twice with unresolved cross-body sketches).
- **Cause:** Accumulated documents in an inconsistent state destabilise CATIA.
- **Rule:** Probe in a scratch script: one new document per variant, closed at the end. Avoid piling failed attempts into one document.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L022 - Name every object at creation (features, sketches, bodies, constraints)

**Severity:** high | **Tools:** `catia_get_tree`, `catia_pad`, `catia_pocket`, `catia_create_sketch`, `catia_new_body`

- **Symptom:** The specification tree is full of Pad.1, Pocket.2, Sketch.3.
- **Cause:** Default names are unreadable and are often judged as poor tree quality.
- **Rule:** Pass 'name' to every creation tool; use role-based names (Sketch_role, Pocket_role_dim, Body_zone). Audit with catia_get_tree and fix remaining defaults.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L027 - CATIA is one shared instance: serialise agents with a lock

**Severity:** high

- **Symptom:** Parallel agents step on each other's documents; one agent's close_all kills another's session.
- **Cause:** Only one CATIA session exists and every call mutates shared state.
- **Rule:** Send all calls through a single queue or lock (a harness), never directly from several agents. Start each scenario with catia_close_all or an explicit open, since another agent may have run in between.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L029 - Inspect before mutating; never invent names, indices, faces or edges

**Severity:** high | **Tools:** `catia_sketch_constraint`, `catia_sketch_get_geometry`

- **Symptom:** A call refers to a feature, sketch geometry index, face or component name that does not exist.
- **Cause:** Names and indices are short-lived observations that change after each mutation.
- **Rule:** Discover references with listing tools right before use (catia_sketch_get_geometry before an index-based constraint, catia_list_components before component names). Re-query after any mutation.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L030 - Do not replay a mutation after a timeout or blocked state

**Severity:** high

- **Symptom:** A call timed out; retrying created a duplicate feature.
- **Cause:** A timeout does not cancel the underlying COM call.
- **Rule:** Stop mutating, inspect connection and document state, restore the last verified state, then continue. Default retry budget: one targeted retry when the cause is known and the fix is reversible.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L140 - close_all in the scripting kit closes other people's documents

**Severity:** high | **Tools:** `catia_close_all`, `catia_describe_model`

- **Symptom:** Documents of another agent vanish from CATIA when a kit script starts; reads of your own document fail at random (Item, Name, Update failed, 'part has no body').
- **Cause:** PartScript and AssemblyScript default to close_all=True (catia_close_all closes every open document, unsaved work included) and any process that closes documents while you read invalidates your COM objects.
- **Rule:** In a shared CATIA session build with close_all=False, close only the documents you opened, verify the part name after every read (the active document can change between two calls) and retry the whole read when a COM call fails.
- **Matches errors:** `La m.thode Item a .chou.`, `La m.thode Name a .chou.`
- **Proof:** Live R19: three documents of another agent were closed by a template run; a later batch of reads needed several retries while other scripts were running in the same CATIA.

### L020 - Default names are localized; detect defaults by the '.N' suffix

**Severity:** medium | **Tools:** `catia_get_tree`, `catia_rename_feature`

- **Symptom:** Features are called Extrusion.N, Poche.N, Révolution.N, Gorge.N, Congé arête.N, Droite.N, 'Corps principal' on a French UI; Pad.1, Sketch.1 in English; BRep names stay English (Brp:(Pad.1;2)).
- **Cause:** Default names depend on the UI language.
- **Rule:** Audit names in a language-independent way: a name ending in '.N' (or the main body name) is a default. Rename at creation via the tool's 'name' argument.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L025 - Run the regression suite after every MCP change, never while using the CATIA view

**Severity:** medium

- **Symptom:** Screenshots show test documents; tests fail after the user rotates the view.
- **Cause:** The suite closes and recreates documents and drives the viewer.
- **Rule:** Keep the regression scenarios all green after any change to the server, and do not manipulate CATIA manually while they run.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L031 - Save, close and export only when required

**Severity:** medium

- **Symptom:** Files overwritten or documents closed unexpectedly.
- **Cause:** Persistence and closing are side effects distinct from modelling.
- **Rule:** Save, close and export only on request and report the actual produced paths. Screenshots are supporting evidence only, never a substitute for measurements.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L032 - Stop the chain on the first failure: do not stack features on a suspect state

**Severity:** medium

- **Symptom:** Several speculative features stacked on an unverified one.
- **Cause:** Later successful updates do not retroactively validate an earlier unchecked feature.
- **Rule:** Follow inspect, mutate, update, measure, decide for each step. Stop on the first failure or contradictory measurement.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L033 - Ambiguous drawing dimensions: measured geometry decides

**Severity:** medium

- **Symptom:** A dimension label (e.g. an 'R' value) seems to point to a different circle than assumed.
- **Cause:** Drawings can be ambiguous and title-block scales can be wrong.
- **Rule:** Measure the geometry of the (vector) drawing, cross-check views (section, top, isometric) and record the chosen interpretation.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L021 - A user name ending in a decimal dimension is not a default name

**Severity:** low | **Tools:** `catia_get_tree`

- **Symptom:** A name like 'Esq_Collar_R57.5' was silently renamed by the naming audit.
- **Cause:** The '.5' looks like an instance counter.
- **Rule:** CATIA defaults never contain '_' and never have a digit right before the dot; apply that rule before renaming.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L026 - Test server changes through a harness that calls the dispatcher, not through reconnects

**Severity:** low

- **Symptom:** Every change requires a slow MCP client reconnect.
- **Cause:** The MCP client caches the server process and tool schemas.
- **Rule:** Drive the real server class from a script (schema listing and scenario runner), so each run re-reads the code. Reconnect the client only once at the end so it sees new schemas.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L034 - Pick a modelling frame that matches the drawing

**Severity:** low

- **Symptom:** Coordinates are confusing and signs flip while translating drawing to CATIA.
- **Cause:** The drawing's origin/axes were not mirrored in the model.
- **Rule:** Choose the drawing's frame (origin on a main axis, revolution axis on Z when possible) and state it in the script docstring. When flipping a coordinate sign, also invert the direction of every arc.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L035 - Capture history by moving InWorkObject, then restore it

**Severity:** low

- **Symptom:** Step-by-step captures need the part at an earlier state.
- **Cause:** Setting Part.InWorkObject = feature and calling Update shows the part at that step with the feature underlined in the tree.
- **Rule:** For step captures, set InWorkObject to each feature in turn, then put the last feature back at the end.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L036 - Threads must be cosmetic; the cylinder keeps the nominal diameter

**Severity:** low | **Tools:** `catia_thread`

- **Symptom:** Modelled threads slow everything and change geometry.
- **Cause:** catia_thread creates a cosmetic thread on the cylindrical face.
- **Rule:** Model the cylinder at the nominal diameter and add catia_thread; do not model the helix.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L037 - Deliver only implemented and verified capabilities

**Severity:** low | **Tools:** `catia_boolean_operation`

- **Symptom:** A guessed method name silently does the wrong thing.
- **Cause:** Some operations (e.g. Remove Lump / 'Retrait de volumes') have no verified Automation signature.
- **Rule:** Report such operations as unsupported and do them in the GUI, rather than shipping a guessed call.
- **Proof:** Remove Lump was left out of the boolean tool for lack of a verified signature.

### L119 - Screenshots are supporting evidence only

**Severity:** low | **Tools:** `catia_screenshot`

- **Symptom:** A plausible screenshot was used as proof.
- **Cause:** A picture cannot prove dimensions.
- **Rule:** Prefer numeric and structural verification (volume, bounding box, parameters, constraint status); use screenshots as a final visual check.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L124 - Expose interface data of each part for later assembly

**Severity:** low | **Tools:** `catia_list_faces`

- **Symptom:** Assembly constraints need axes and faces of each part.
- **Cause:** Assembly designation works with points in the part's own coordinates.
- **Rule:** Write down for each part axes (point and direction), seating faces (a point inside the face) and bores in its own frame; use catia_list_faces for inside_point and cylinder axes/radii.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L152 - Audit tools that read features can invalidate them (false 'not up to date')

**Severity:** low | **Tools:** `catia_audit_model`

- **Symptom:** An audit reports many features as not up to date on files that are fine.
- **Cause:** Reading some feature properties through COM marks the feature for update.
- **Rule:** Do not treat 'not up to date' from an inspection tool as an error on its own: re-read the file without the audit and update once before saving.
- **Proof:** Live R19: 105 'not up to date' findings vanished when the same files were read without the audit.

## Reading drawings

### L120 - Verify a part visually at the drawing's viewing angle before declaring it finished

**Severity:** high | **Tools:** `catia_set_view`, `catia_screenshot`

- **Symptom:** Part matches numbers but differs from the drawing (missing detail or wrong feature).
- **Cause:** Numeric checks miss shape differences.
- **Rule:** Compare the CATIA capture with the drawing's isometric view, using the same viewing angle (catia_set_view isometric variants). Everything drawn must be present, nothing extra.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L123 - Overlay the profile on the drawing at high resolution

**Severity:** high | **Tools:** `catia_sketch_profile`

- **Symptom:** A profile looks right in CATIA but deviates from the drawing.
- **Cause:** Only an overlay reveals arc side errors and small offsets.
- **Rule:** Crop the drawing view at high resolution (about 400 dpi), draw the profile on top in a contrasting colour, and check the coloured line follows the black line everywhere.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L125 - A drawing view is empty unless its GenerativeBehavior.Document is set on every view

**Severity:** high | **Tools:** `catia_drawing_add_view`

- **Symptom:** The view exists, no error is raised, the exported PDF shows nothing for it.
- **Cause:** A generative view only draws once view.GenerativeBehavior.Document points to the 3D source; this is needed on the front view AND on every projected, section and detail view (DefineProjectionView alone is not enough).
- **Rule:** Set GenerativeBehavior.Document = <CATPart|CATProduct>.Product on each new view, then Update. Check the view size (View.Size through a VBScript) or the PDF, never only the absence of an error.
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

### L127 - Factory2D of a drawing view fails until the view is activated

**Severity:** high | **Tools:** `catia_drawing_title_block`, `catia_drawing_add_centerlines`

- **Symptom:** CreateLine / CreatePoint / CreateClosedCircle raise a generic E_FAIL on a view that is not the active one.
- **Cause:** 2D creation works on the active view only (the Main View is active in a new drawing).
- **Rule:** Call view.Activate() before Factory2D.Create*, and re-activate the Main View afterwards. The frame and title block go in the Background View.
- **Matches errors:** `CATIAFactory2D`, `Factory2D`
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

### L128 - Generated drawing edges cannot be dimensioned from Automation: dimension helper geometry

**Severity:** high | **Tools:** `catia_drawing_add_dimension`

- **Symptom:** Selection.Search finds no generated line or circle and DrawingDimensions.Add needs geometry objects.
- **Cause:** Search on a drawing only returns user 2D geometry, views and texts; the projected edges are not scriptable.
- **Rule:** Create hidden Factory2D geometry exactly on the model geometry (SetShow(1)) and dimension it; place the line with DrawingDimension.MoveValue(x, y, 0, 0) (the pick points passed to Add do not place it). Take the values from the 3D model and let catia_drawing_check compare them with the PDF.
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

### L132 - catia_mcp.drawing.extract misses large circles exported as hundreds of chords

**Severity:** high | **Tools:** `catia_drawing_check`

- **Symptom:** A drawing exported by CATIA shows circles of radius 5 mm and more missing from extract() while 3 mm ones are found.
- **Cause:** CATIA writes a circle as a polyline of short chords; the PDF coordinate jitter flips the sign of the tiny turning angles and the smoothness test rejects the chain.
- **Rule:** Use catia_mcp.drawing.verify.find_circles / polyline_circles, which accept a chain by its total turning and fit residual. Compare diameters within 0.02 mm and centres within 0.05 mm.
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

### L121 - Read scale and dimensions from the drawing with a measured geometry pass

**Severity:** medium

- **Symptom:** Dimensions transcribed from a raster drawing are off; the title-block scale contradicts the drawing.
- **Cause:** Title-block scales can be wrong and printed dimensions ambiguous.
- **Rule:** Extract exact geometry from a vector PDF (circles, arcs with centre and radius, segments with length and angle), compare with the written dimensions, and note title-block scale conflicts. An unwritten dimension is measured, since the drawing is to scale.
- **Proof:** Applied on dozens of parts; several title-block scale conflicts (e.g. 1:8 stated, 1:10 drawn) were found this way.

### L122 - Cross-check drawing views (section, top, isometric) before modelling

**Severity:** medium

- **Symptom:** Modelled shape contradicts one of the views.
- **Cause:** Each view carries different information.
- **Rule:** Reconcile all views; choose the interpretation consistent with all of them and document it.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L126 - New drawing views start at (0, 0) with scale 1: place and scale them yourself

**Severity:** medium | **Tools:** `catia_drawing_add_view`

- **Symptom:** Projected views pile up at the sheet origin at 1:1 whatever the sheet scale.
- **Cause:** Sheet.Scale is not inherited by views and CATIA does not lay projected views out from Automation.
- **Rule:** Set View.Scale, View.x and View.y for every view; View.x/.y is the centre of the projected bounding box and xAxisData/yAxisData the sheet position of the projected model origin.
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

### L129 - Sheet.GenerateDimensions only creates the dimensions driven by 3D constraints

**Severity:** medium | **Tools:** `catia_drawing_generate_dimensions`, `catia_drawing_add_dimension`

- **Symptom:** After GenerateDimensions the overall sizes and hole positions are missing.
- **Cause:** Only the constraints of the 3D features (pad lengths, hole diameters) become dimensions, drawn green.
- **Rule:** Use GenerateDimensions as a base, then add what is missing with catia_drawing_add_dimension; do not repeat a generated dimension (ISO 129-1: dimension a feature once).
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

### L130 - A new drawing sheet has no readable format until PaperSize is set

**Severity:** medium | **Tools:** `catia_drawing_create`

- **Symptom:** Sheet.PaperSize, Orientation, PaperName, GetPaperWidth raise a generic error on a fresh drawing.
- **Cause:** The default sheet carries no paper format until one is assigned.
- **Rule:** Set PaperSize (CatPaperSize: A0=2, A1=3, A2=4, A3=5, A4=6) then Orientation (0 portrait, 1 landscape), then read the size back with GetPaperWidth/GetPaperHeight and compare it with ISO 5457.
- **Matches errors:** `CATIADrawingSheet.*(PaperSize|Orientation|PaperName|GetPaper)`
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

### L131 - DrawingView.Size returns zeros from Python: read it through a VBScript

**Severity:** medium | **Tools:** `catia_drawing_add_view`

- **Symptom:** view.Size(...) raises 'Objects for SAFEARRAYS must be sequences' or gives zeros.
- **Cause:** Size fills a ByRef array, which late binding does not write back (same family as GetCOG).
- **Rule:** Run a VBScript in CATIA: app.SystemService.Evaluate(code, 0, 'CATMain', [view]) with Dim a(3): v.Size a and return Array(a(0), a(1), a(2), a(3)) = (xmin, xmax, ymin, ymax) on the sheet.
- **Matches errors:** `Objects for SAFEARRAYS must be sequences`
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

### L134 - Another client can close the drawing between two calls: re-find it by name and say so

**Severity:** medium | **Tools:** `catia_drawing_info`

- **Symptom:** Sheets.ActiveSheet or ExportData fail with E_UNEXPECTED (-2147418113) on a drawing that was open a second ago.
- **Cause:** Documents are shared by every client of the CATIA session; catia_close_all from another agent closes them all.
- **Rule:** Do not rely on ActiveDocument: look the drawing up by name at each call, and when it is gone report that it was closed and rebuild it. Serialise scripts with the cross-process lock (--lock).
- **Matches errors:** `-2147418113`, `CATIADrawingSheets.*ActiveSheet`
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

### L133 - CATIA drawing PDFs draw text as strokes: glyphs look like 1.3 mm circles

**Severity:** low | **Tools:** `catia_drawing_check`, `catia_drawing_export_pdf`

- **Symptom:** A circle extraction of a PDF exported by DrawingDocument.ExportData returns small circles at label positions.
- **Cause:** Letters such as o, 0 and 6 are exported as vector strokes, not as text.
- **Rule:** Ignore circles below 2 mm in diameter when checking a drawing, and never read the scale or dimension values from the PDF text: pass the scale explicitly and read dimension values from CATIA (GetValue).
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

### L135 - A dimension prefix replaces CATIA's diameter symbol unless the default prefix is kept

**Severity:** low | **Tools:** `catia_drawing_add_dimension`

- **Symptom:** SetPSText(1, '4x ', '') turns 'diameter 8' into '4x 8'.
- **Cause:** The default main prefix is the placeholder <DIAMETER> (<RADIUS> for radii); overwriting it drops the symbol.
- **Rule:** Read GetPSText(1, '', '') and write your prefix followed by the default one: '4x <DIAMETER>'.
- **Proof:** Verified live on CATIA V5 R19 (French UI) while building the drafting tools.

## Performance

### L028 - Keep scenarios short and never list all faces of a dense part

**Severity:** high | **Tools:** `catia_list_faces`, `catia_list_edges`

- **Symptom:** Listing faces/edges of a dense part (grooved tyre, wheel rims, big patterns) takes minutes at 100 % CPU and blocks everyone.
- **Cause:** Topology enumeration costs about 50 ms to 250 ms per element.
- **Rule:** Target a precise point instead of enumerating; limit listings to small parts or a focused search.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L082 - Geometry designation costs about 50 ms per candidate

**Severity:** medium | **Tools:** `catia_fillet`, `catia_chamfer`, `catia_coincidence_constraint`

- **Symptom:** Edge designation on a dense part takes tens of seconds.
- **Cause:** Each GetMinimumDistance costs about 50 ms inside CATIA; 900 edges can take 45 s.
- **Rule:** Typical parts take 1 to 3 s. Avoid designating on very dense parts in loops.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L084 - Avoid hundreds of topological searches just to verify positions

**Severity:** medium

- **Symptom:** The CATIA selection flickered for 90 s after 144 topological searches used to check positions.
- **Cause:** Each search updates the selection highlight.
- **Rule:** Verify with ONE search plus a read, or with the body volume.
- **Proof:** Verified live on CATIA V5 R19 (French UI).

### L150 - Designation of faces on dense parts is slow; cache it on disk and batch it

**Severity:** medium | **Tools:** `catia_prepare_geometry`, `catia_coincidence_constraint`

- **Symptom:** One dense cylinder costs about 6 s per designated point, and every new process pays it again (a 70-component assembly took 13 minutes).
- **Cause:** Each designation opens the part and searches its topology; the in-memory cache dies with the process.
- **Rule:** Use catia_prepare_geometry (one opening per file) and keep the persistent designation cache on (CATIA_MCP_DESIGNATION_CACHE_DISK, default on; keys include file mtime and size, so edited parts are never served stale). Give few points on heavy parts and prefer axes and planar faces to edges.
- **Proof:** Live R19: the same 70-component assembly went from 780 s to 164 s once the designations were cached from the previous run.

### L151 - Timings on the same machine vary up to 3x: compare medians, not single runs

**Severity:** low

- **Symptom:** A change seems to make a build 3 times slower or faster.
- **Cause:** Background load, window redraws and CATIA housekeeping add large noise to identical runs.
- **Rule:** Before blaming or crediting a change, alternate base and new runs several times and compare medians; keep instrumentation (CATIA_MCP_PROFILE) to see where the time goes.
- **Proof:** Live R19: an apparent regression (15 s to 45 s) disappeared when base and new runs were interleaved.
