# The test part of SPEC AC-2 in CadQuery: a 40 x 30 x 10 mm block with a 8 mm through-hole.
import cadquery as cq

length, width, thickness = 40.0, 30.0, 10.0
hole = 8.0

part = (
    cq.Workplane("XY")
    .box(length, width, thickness, centered=False)
    .faces(">Z")
    .workplane()
    .center(length / 2, width / 2)
    .hole(hole)
)

show_object(part)
