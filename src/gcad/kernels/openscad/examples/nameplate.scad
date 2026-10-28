// A name plate, rendered by your installed OpenSCAD.
// text() and minkowski() are OpenSCAD's own; GCAD Studio receives the finished mesh.
$fn = 32;

width = 64;
depth = 20;

difference() {
    minkowski() {
        cube([width, depth, 2]);
        cylinder(r = 3, h = 1);
    }
    translate([5, 5, 2])
        linear_extrude(2)
            text("GCAD", size = 10);
}
