// Terradio case: a desk radio with a gently leaning front and the trackball on top.
// About 108 wide x 94 deep x 114 tall (mm).
//
// Open this file in OpenSCAD (free, openscad.org). Press F5 to preview, F6 to
// render, then File > Export > STL to print. Pick what to show or export with
// `part` below. Every size you might need to change is a named number in the
// "Measure these" and "Shape" sections: change it, press F5, and the case
// follows.
//
// Units are millimeters. Sizes marked "(measure)" are best guesses from the
// datasheets; measure the real part when it arrives and update the number.
//
// Coordinates: x runs across the front (left to right), y runs from the front
// to the back, z runs up. The front panel leans back by `tilt` degrees.

/* [What to show or export] */
// "assembly" previews everything together. The others are the printable parts.
part = "assembly"; // [assembly, inside, cutaway, body, front, back, face_clip, test_holes, test_face, test_ball, test_usbc, test_screws]

/* [Measure these when the parts arrive] */
// The face: Waveshare ESP32-S3-LCD-2.8C, No Touch (sizes from Waveshare's
// drawing). Its round LCD is bigger than the round board behind it, and the
// LCD's ribbon folds round its bottom edge to the back of the board.
face_glass_d   = 73.0;   // LCD diameter (73.00 +/- 0.15)
face_view_d    = 70.13;  // visible picture diameter
face_play      = 1.5;    // how much wider than the LCD its pocket and collar are (0.5 was
                         // a hair tight: printed holes come out a little small)
face_pcb_d     = 66.0;   // the board behind the LCD
face_lcd_t     = 3.0;    // (measure) LCD thickness: its front to its back, where the board starts
face_stack     = 12;     // (measure) LCD front to the back of the tallest part on the board
face_tail_w    = 41;     // width of the notch for the LCD's bottom: the glass isn't quite round there,
                         // it widens into a tab where the ribbon leaves (35 caught the tab's shoulders)
face_tail      = 4;      // room left below the LCD's edge for that fold: where the notch's flat
                         // bottom edge sits (6 left a gap below the real screen in the test ring)
face_clip_angles = [0, 90, 180];  // where the clips press on the LCD's back rim (0 = right,
                                    // 90 = top, seen from the front): the board has flat edges
                                    // there, leaving the most bare metal and no parts at its edge.
                                    // Three are enough: with the bezel in front, the bottom can't
                                    // lift unless the top pushes forward through it
face_clip_t    = 2.5;    // clip thickness, so a 6 mm screw stops 0.5 mm short in its 4 mm hole
face_clip_nub  = 1.0;    // (measure) how far below the collar's top the LCD's metal back rim sits:
                         // a nub this tall at each clip's tip reaches down and presses on it
knob_hole_d    = 7.4;    // EC11 threaded bushing is 7 mm
encoder_tab    = false;  // cut a slot for the encoder's anti-rotation tab?
encoder_tab_offset = 7.8;   // (measure) tab distance from the shaft center
// The trackball: a BlackBerry-style ball on a 28 x 22 mm breakout board. A thin
// skin of case covers its octagonal metal housing, which sits snug in a pocket
// with its top flat against the skin; the ball and the raised metal lip round it
// come up through a round hole. Measure the board held with its row of pins at
// the top and the labels the right way up.
trackball_ball_d = 5.0;           // (measure) the ball's diameter
trackball_ball_top = 4.9;         // (measure) height of the ball's top above the board
trackball_housing = [9, 9];       // the octagonal metal housing round the ball, across its flats
trackball_housing_cut = 1.5;      // (measure) how much the housing's corners are cut off, along each edge
trackball_housing_h = 3.4;        // (measure) height of the housing's flat top above the board
trackball_play = 0.5;             // how much wider than the housing its pocket is
trackball_lip_d = 7.5;            // (measure) the raised metal lip round the ball, across its outside
trackball_skin = 0.8;             // case left over the housing (the ball sticks out this much less)
trackball_board = [28, 22];       // board width x height
trackball_ball_at = [12.8, 8.1];  // (measure) the ball's center, from the board's bottom-left corner
trackball_holes = [[-10.3, -6.1], [12.6, -6.1]]; // (measure) its 2 mounting holes, from the ball's center
trackball_pins_y = 12.8;          // (measure) the row of pins, up from the ball's center
trackball_base = [14.5, 13.5];    // (measure) the module's whole footprint round the ball: its black
                                  // base and solder tabs stick out past the housing
trackball_base_h = 2.0;           // (measure) how tall that base and its tabs stand on the board
speaker_frame  = 53;     // AUIN 2" speaker: square frame (listing drawing)
speaker_holes  = 42.4;   // (measure) spacing of its corner screw holes (60 mm diagonal)
speaker_pilot  = 2.2;    // pilot hole for its M2.5 screws to cut their own thread
speaker_boss_h = 4;      // how far the bosses hang below the top
speaker_depth  = 34;     // its depth, frame to the back of the magnet
speaker_magnet = 32;     // its magnet's diameter
speaker_cone   = 46;     // the moving cone's diameter, which the grille covers
// The USB-C power socket: its mouth is flush with the front of a metal plate,
// which sits in a pocket on the back panel's inside behind a thin skin.
usbc_plate     = [20.1, 6.7];     // its mounting plate, width x height (listing drawing)
usbc_plate_t   = 1.0;    // (measure) the plate's thickness
usbc_skin      = 0.8;    // panel left in front of the plate
usbc_window_w  = 13;     // the window in the skin, round the socket's mouth: wide enough for most
                         // plugs' bodies, leaving 3.5 mm of skin over each end of the plate. A plug
                         // body wider than this stops at the skin, so it goes in usbc_skin less
                         // than all the way
pi_holes       = [58, 23];        // Raspberry Pi Zero mounting holes (fixed by the Pi)

/* [Shape] */
tilt        = 10;    // front panel lean, degrees back from vertical
width       = 108;   // outside width (the speaker and trackball side by side on top need 102)
front_len   = 116;   // length of the front panel along its slope
depth       = 94;    // outside depth at the bottom
wall        = 2.4;   // wall thickness (a multiple of your nozzle width is best)
front_t     = 3;     // front panel thickness (EC11 bushing threads need <= 5)
back_t      = 3;     // back panel thickness
bezel       = 1.8;   // material left in front of the screen glass
face_top_margin = 13;    // gap from the top of the front panel to the screen
controls_v  = 14;    // height of the knobs up the front panel (in the
                     // bottom corners beside the round face)
knob_spacing = 38;   // knobs' distance either side of the middle
ball_on_top = true;  // true: the trackball sits on the top, beside the speaker grille
                     // (front right). false: on the front, between the knobs
edge_r      = 3;     // radius of every outside edge
reveal      = 0.8;   // width of the shadow line where each panel meets the body
window_bevel = 1.0;  // depth of the 45-degree bevel round the screen window

/* [Hardware] */
// Every screw is a nylon M2.5 x 6 mm pan head from the standoff kit. The back
// panel and the front panel's feet screw into the kit's nuts, pushed into slots;
// the rest cut their own thread into a pilot hole.
m25_clear     = 2.8;  // M2.5 clearance
m25_pilot     = 2.2;  // pilot for an M2.5 screw to cut its own thread
m25_head_d    = 5.4;  // room for an M2.5 pan head (4.5-5 mm across)
m25_head_h    = 2.2;  // and its height (about 1.8 mm)
m25_nut_af    = 5.0;  // (measure) the kit's nuts, across their flats
m25_nut_t     = 2.0;  // (measure) and their thickness
nut_roof      = 0.6;  // plastic between a nut's slot and the face its screw comes in through
post          = 9;    // corner posts for the back panel's screws
post_len      = 10;
foot_spacing  = 22;   // the front panel's two feet: distance either side of the middle
foot_w        = 10;   // foot width
foot_len      = 12;   // how far back from the front panel each foot reaches
foot_h        = 10;   // foot height
pad_h         = 1.6;  // floor pads under the feet, so the screws' heads sit in the floor
bed           = 220;  // your printer bed size, for the size check

$fn = 96;
eps = 0.01;

// Derived sizes
H  = front_len * cos(tilt);        // height
Ls = front_len * sin(tilt);        // how far back the front's top edge sits
face_v = front_len - face_top_margin - face_glass_d / 2;   // screen center, up the front panel
collar_wall = 2.4;
collar_ri = (face_glass_d + face_play) / 2;
collar_ro = collar_ri + collar_wall;
// The collar stands out from the panel's back to the LCD's back, where the clips press.
collar_h = max(0.8, bezel + face_lcd_t - front_t);
clip_r = collar_ro + 2.5;     // the clips' screw bosses, from the screen's center
speaker_y = depth - back_t - speaker_frame / 2 - 4;   // toward the back, clear of the face collar
// With the ball on top: its center, between the grille and the right side,
// behind the top socket and its ramp.
ball_top = [width - wall - 1 - trackball_ball_at[1], Ls + front_t + post_len + 23];
// The front panel's hidden fixing (see "front panel fixing" below)
foot_x  = [width / 2 - foot_spacing, width / 2 + foot_spacing];
foot_z0 = wall + pad_h;               // the feet's bottoms, resting on the floor pads
tongue_x = [wall + 9, width - wall - 9];
tongue_z = H - wall - 3.5;            // the tongues' middle, height
// How far back the front panel's inside face is at height z
function panel_back_y(z) = (z + front_t * sin(tilt)) * tan(tilt) + front_t * cos(tilt);
foot_screw_y = panel_back_y(foot_z0 + foot_h) + 4;   // the feet's screws, from the front

// ---------------------------------------------------------------- frames

// Front panel frame: u across, v up the slope, w outward (out of the front).
// The front panel's outer surface is w = 0; it fills w from -front_t to 0.
module front_frame() {
    multmatrix([[1, 0, 0, 0],
                [0, sin(tilt), -cos(tilt), 0],
                [0, cos(tilt),  sin(tilt), 0],
                [0, 0, 0, 1]]) children();
}

// The inverse of front_frame: model coordinates into the front panel's own frame.
module model_to_front() {
    multmatrix([[1, 0, 0, 0],
                [0, sin(tilt), cos(tilt), 0],
                [0, -cos(tilt), sin(tilt), 0],
                [0, 0, 0, 1]]) children();
}

// Model coordinates into the back panel's flat frame (outer face at z = 0).
module model_to_back() {
    rotate([-90, 0, 0]) translate([0, -depth, 0]) children();
}

// ---------------------------------------------------------------- outer shape

// The case's outside: the side profile (a wedge) with every outside edge rounded
// to edge_r. It's the hull of a ball at each corner, so `inset` shrinks the whole
// shape evenly: that gives the inside of the walls and the shadow lines. The body
// and both panels are all cut from it, so their edges match.
function corner_yz() = [
    [edge_r * (front_len + Ls) / H, edge_r],
    [depth - edge_r, edge_r],
    [depth - edge_r, H - edge_r],
    [(edge_r * front_len + (H - edge_r) * Ls) / H, H - edge_r]];

module rounded_solid(inset = 0) {
    hull() for (x = [edge_r, width - edge_r], c = corner_yz())
        translate([x, c[0], c[1]]) sphere(r = max(edge_r - inset, 0.01), $fn = 40);
}

// A shadow line where each panel meets the body: a groove `reveal` wide and 0.6 mm
// deep, half in the panel and half in the body. It makes the seam look deliberate
// and hides small differences between the printed parts.
module reveal_cuts() {
    difference() {
        union() {
            front_frame() translate([-1, -50, -front_t - reveal / 2]) cube([width + 2, front_len + 100, reveal]);
            translate([-1, depth - back_t - reveal / 2, -1]) cube([width + 2, reveal, H + 2]);
        }
        rounded_solid(0.6);
    }
}

// ---------------------------------------------------------------- body

module body() {
    difference() {
        union() {
            difference() { rounded_solid(); rounded_solid(wall); }
            intersection() {
                rounded_solid();
                union() { floor_pads(); top_sockets(); back_posts(); speaker_bosses(); }
            }
            pi_pads();
            if (ball_on_top) top_ball_frame() trackball_bosses(wall);
        }
        // The front and back panels are separate parts: clear their space.
        front_frame() translate([-1, -50, -front_t]) cube([width + 2, front_len + 100, front_t + 50]);
        translate([-1, depth - back_t, -1]) cube([width + 2, back_t + 1, H + 2]);
        reveal_cuts();
        floor_pad_holes();
        top_socket_slots();
        back_post_holes();
        speaker_grille();
        speaker_screw_holes();
        pi_holes_cut();
        // (the body prints standing on its back, so "up" is toward the front: the
        // ball frame's -x)
        if (ball_on_top) top_ball_frame() { trackball_cut(wall, up = [-1, 0]); trackball_inside_cuts(wall, up = [-1, 0]); }
    }
}

// ---------------------------------------------------------------- front panel fixing
// No screws show on the front. Near the top, two tongues on the panel's back slide
// into sockets under the top wall; at the bottom, two feet on its back rest on pads
// on the floor and take a screw each from underneath, into a nut in a slot.
// To fit the panel, slide it straight back (level, not along its slope) until the
// tongues are home, then screw it down from below. The feet's screws also stop the
// panel tipping forward.

// The body's half: floor pads and the top sockets. The body prints standing on its
// open back, so each socket has a ramp behind it up to the top wall (no supports).
module floor_pads() {
    for (fx = foot_x)
        translate([fx - foot_w / 2 - 1.5, 2, 0]) cube([foot_w + 3, panel_back_y(foot_z0) + foot_len + 1, foot_z0]);
}

// Counterbored, so the screw heads sit flush with the bottom and the radio stands level.
module floor_pad_holes() {
    for (fx = foot_x) translate([fx, foot_screw_y, 0]) {
        translate([0, 0, -1]) cylinder(d = m25_clear, h = foot_z0 + 2, $fn = 32);
        translate([0, 0, -1]) cylinder(d = m25_head_d, h = 1 + m25_head_h, $fn = 32);
    }
}

// A slot for one of the kit's nuts, its axis along z from z = 0, open toward +x
// out to `len`: push the nut in until it stops, and it sits centered on z. Its
// flats face the slot's sides, so it can't turn.
module nut_slot(len) {
    r = m25_nut_af / sqrt(3) + 0.15;   // the nut's corners, with a little room
    w = m25_nut_af + 0.2;
    translate([-r, -w / 2, 0]) cube([r + len, w, m25_nut_t + 0.3]);
}

module top_sockets() {
    sy = panel_back_y(tongue_z) + 0.2;   // front face, just behind the panel
    for (tx = tongue_x) {
        x0 = tx < width / 2 ? 0 : tx - 7;   // reaches the side wall
        x1 = tx < width / 2 ? tx + 7 : width;
        hull() {
            translate([x0, sy, tongue_z - 3.5]) cube([x1 - x0, 10, H - tongue_z + 3.5]);
            translate([x0, sy + 10, H - wall - eps]) cube([x1 - x0, 8, wall]);
        }
    }
}

module top_socket_slots() {
    for (tx = tongue_x)
        translate([tx - 5, panel_back_y(tongue_z) - 1, tongue_z - 1.2]) cube([10, 10, 2.4]);
}

// The panel's half: the feet and the tongues, in model coordinates.
module front_feet() {
    for (fx = foot_x) difference() {
        translate([fx - foot_w / 2, 0, foot_z0]) cube([foot_w, panel_back_y(foot_z0) + foot_len, foot_h]);
        front_frame() translate([-1, -50, -front_t + 0.5]) cube([width + 2, front_len + 100, 50]);   // overlaps the panel 0.5 mm
    }
}

// Each foot takes a nut in a slot 1.5 mm above its bottom, pushed in from the
// foot's back end. The front panel prints face down, so the screw holes print
// sideways with "up" toward the back (teardrop-topped), and the slots stand
// open at the top, needing no bridge.
module front_feet_holes() {
    for (fx = foot_x) translate([fx, foot_screw_y, foot_z0]) {
        translate([0, 0, -eps]) sideways_hole(m25_clear, 1.5 + m25_nut_t + 0.3 + 1.5, [0, 1], clear = m25_clear / 2 * (sqrt(2) - 1), $fn = 32);
        translate([0, 0, 1.5]) rotate([0, 0, 90]) nut_slot(foot_len + 5);
    }
}

module front_tongues() {
    for (tx = tongue_x) difference() {
        translate([tx - 4.7, 0, tongue_z - 0.95]) cube([9.4, panel_back_y(tongue_z) + 7, 1.9]);
        front_frame() translate([-1, -50, -front_t + 0.5]) cube([width + 2, front_len + 100, 50]);   // overlaps the panel 0.5 mm
    }
}

function back_post_spots() = [
    [wall + post / 2, wall + post / 2],
    [width - wall - post / 2, wall + post / 2],
    [wall + post / 2, H - wall - post / 2],
    [width - wall - post / 2, H - wall - post / 2]];

module back_posts() {
    for (p = back_post_spots())
        translate([p[0] - post / 2, depth - back_t - post_len, p[1] - post / 2]) cube([post, post_len, post]);
}

// Each post takes a nut in a slot just behind its face, pushed in from the
// side facing the middle of the case.
module back_post_holes() {
    for (p = back_post_spots()) translate([p[0], depth - back_t + eps, p[1]]) rotate([90, 0, 0]) {
        cylinder(d = m25_clear, h = nut_roof + m25_nut_t + 0.3 + 1.5);
        translate([0, 0, nut_roof]) mirror([p[0] < width / 2 ? 0 : 1, 0, 0]) nut_slot(post);
    }
}

// Four bosses under the top for the speaker's corner screws: the speaker
// faces up into the grille, screwed to the bosses with M2.5 screws.
module speaker_bosses() {
    for (x = [-1, 1], y = [-1, 1])
        translate([width / 2 + x * speaker_holes / 2, speaker_y + y * speaker_holes / 2, H - wall - speaker_boss_h])
            cylinder(d = 6.5, h = speaker_boss_h + eps);
}

// Their screw holes run up into the top wall, stopping 0.8 mm short of the
// outside so nothing shows: about 5.5 mm of thread for each screw. The body prints
// standing on its back, so they print sideways, with "up" toward the front.
module speaker_screw_holes() {
    for (x = [-1, 1], y = [-1, 1])
        translate([width / 2 + x * speaker_holes / 2, speaker_y + y * speaker_holes / 2, H - wall - speaker_boss_h - 1])
            pilot_hole(speaker_pilot, speaker_boss_h + 1 + wall - 0.8, [0, -1]);
}

// A pilot hole for a screw to cut its own thread into, along z. Printed sideways
// (`up` is the direction that faces up on the printer), its top edge would sag and
// pinch the screw, so it gets a short flat top 0.3 mm above the circle.
module pilot_hole(d, h, up = [0, 0]) {
    if (up == [0, 0]) cylinder(d = d, h = h, $fn = 24);
    else sideways_hole(d, h, up, $fn = 24);
}

// Grille: a honeycomb of hexagonal holes in the top, over the speaker. Each
// hexagon has a flat side toward the front, which is "up" while the body prints
// standing on its back: its other sides stand at 60 degrees and hold themselves
// up, and the flat top is a 1.75 mm bridge, so every hole prints crisp.
module speaker_grille() {
    r = speaker_cone / 2 - 2;
    step = 4.6;
    translate([width / 2, speaker_y, H - wall - 1])
        for (i = [-8 : 8], j = [-8 : 8]) {
            x = i * step * 0.866;
            y = j * step + (abs(i) % 2) * step / 2;
            if (x * x + y * y <= r * r) translate([x, y, 0]) grille_hole(wall + 2);
        }
}
module grille_hole(h) { cylinder(r = 1.75, h = h, $fn = 6); }   // points along x, flats along y

// A hole that prints well sideways: round, with 45-degree sides rising to a
// short flat `clear` above the circle, which bridges cleanly where a curve would
// sag (or to a point, for clear = r * (sqrt(2) - 1)). `up` is the direction that
// faces up on the printer, in the hole's own x-y plane.
module sideways_hole(d, h, up, clear = 0.3) {
    r = d / 2;
    top = min(r + clear, r * sqrt(2));
    w = max(2 * (r * sqrt(2) - top), 0.02);
    hull() {
        cylinder(d = d, h = h);
        rotate([0, 0, atan2(up[1], up[0])]) translate([top - 0.01, -w / 2, 0]) cube([0.01, w, h]);
    }
}

// Raspberry Pi Zero: four M2.5 holes in the floor for nylon standoffs,
// screwed from underneath (counterbored so the screw heads sit flush). Far
// enough back that the face's angled USB plug, which turns back into the case
// at the screen's lower left, clears the jumper plugs on the Pi's header
// (checked in build_preview.scad).
pi_origin = [width / 2 - pi_holes[0] / 2, max(depth - 60, front_t + face_stack + 8)];
module pi_pads() {
    for (x = [0, pi_holes[0]], y = [0, pi_holes[1]])
        translate([pi_origin[0] + x, pi_origin[1] + y, 0]) cylinder(d = 7, h = wall + 1);
}
module pi_holes_cut() {
    for (x = [0, pi_holes[0]], y = [0, pi_holes[1]])
        translate([pi_origin[0] + x, pi_origin[1] + y, 0]) {
            translate([0, 0, -1]) cylinder(d = m25_clear, h = wall + 3);
            translate([0, 0, -1]) cylinder(d = 5.2, h = 1 + 1.4);
        }
}

// ---------------------------------------------------------------- front panel
// Built in its own frame (u, v, w): outer surface at w = 0, back at w = -front_t.

// The front panel in place, in model coordinates: a plate cut from the rounded
// outside (so its edges match the body's), with the screen collar, the hidden
// feet and tongues, and no screws on the front.
module front_panel_model() {
    difference() {
        union() {
            intersection() {
                rounded_solid();
                front_frame() translate([-1, -1, -front_t]) cube([width + 2, front_len + 2, front_t]);
                front_frame() face_chamfer();
            }
            front_frame() face_collar();
            if (!ball_on_top) front_frame() translate([width / 2, controls_v, 0]) trackball_bosses();
            front_feet();
            front_tongues();
        }
        front_frame() front_panel_holes();
        front_feet_holes();
        reveal_cuts();
    }
}

// The panel prints face down, where the first, nearly flat part of a rounded
// edge would droop. So the rounded edges meet the front face with a small
// 45-degree chamfer (tangent to the round) instead, in the panel's own frame.
module face_chamfer() {
    k = edge_r * (2 - sqrt(2));
    hull() {
        translate([k, k, -eps]) cube([width - 2 * k, front_len - 2 * k, eps]);
        translate([0, 0, -50]) cube([width, front_len, 50 - k]);
    }
}

// The same panel in its own frame (u across, v up the slope, w out of the front).
module front_panel_local() {
    model_to_front() front_panel_model();
}

// Holes through the front panel, in its own frame.
module front_panel_holes() {
    // Screen window with a 45-degree bevel on the front, and a pocket on the back
    // that centers the LCD.
    translate([width / 2, face_v, -front_t - 1]) cylinder(d = face_view_d + 0.6, h = front_t + 2);
    translate([width / 2, face_v, -window_bevel])
        cylinder(d1 = face_view_d + 0.6, d2 = face_view_d + 0.6 + 2 * window_bevel, h = window_bevel + eps);
    translate([width / 2, face_v, -front_t - eps]) linear_extrude(front_t - bezel + eps) face_outline();
    // Knobs
    for (s = [-1, 1]) translate([width / 2 + s * knob_spacing, controls_v, -front_t - 1]) {
        cylinder(d = knob_hole_d, h = front_t + 2);
        if (encoder_tab) translate([-0.8, encoder_tab_offset - 1.4, 0]) cube([1.6, 2.8, front_t - 1]);
    }
    // Trackball: a snug round hole for the ball, its housing hidden behind
    if (!ball_on_top) translate([width / 2, controls_v, 0]) { trackball_cut(); trackball_inside_cuts(); }
    // Room for the LCD's ribbon where it folds round the bottom edge
    translate([width / 2 - face_tail_w / 2, face_v - face_glass_d / 2 - face_tail, -front_t - collar_h - 1])
        cube([face_tail_w, face_tail + face_glass_d / 2, front_t + collar_h + 1 - bezel]);   // up into the pocket, however wide
    // Pilot holes for the clips' screws, stopping short of the front
    for (a = face_clip_angles)
        translate([width / 2 + cos(a) * clip_r, face_v + sin(a) * clip_r, -front_t - collar_h - 1])
            cylinder(d = m25_pilot, h = collar_h + front_t + 1 - 0.8, $fn = 24);
}

// The LCD's outline with room round it, centered on the screen: round, except
// that the glass flares out at the bottom into a tab where the ribbon leaves, so
// the outline runs straight from the circle out to the notch's corners.
module face_outline(grow = 0) {
    offset(r = grow) hull() {
        circle(r = collar_ri);
        translate([-face_tail_w / 2, -(face_glass_d / 2 + face_tail)]) square([face_tail_w, face_glass_d / 2 + face_tail]);
    }
}

// A low collar on the back of the front panel that the LCD drops into, with a
// boss at each clip position. The LCD's back is level with the collar's top.
module face_collar() {
    translate([width / 2, face_v, -front_t - collar_h]) {
        difference() {
            union() {
                linear_extrude(collar_h + eps) face_outline(collar_wall);
                for (a = face_clip_angles)
                    translate([cos(a) * clip_r, sin(a) * clip_r, 0]) cylinder(d = 6, h = collar_h + eps);
            }
            translate([0, 0, -1]) linear_extrude(collar_h + 2) face_outline();
        }
    }
}

// How far the board sits behind the panel's back, so the housing's top sits flat
// against the skin, and how far the ball then sticks out.
function trackball_gap_for(t) = max(0, trackball_housing_h + trackball_skin - t);
trackball_gap = trackball_gap_for(ball_on_top ? wall : front_t);
trackball_proud = trackball_ball_top - trackball_housing_h - trackball_skin;
trackball_wall_left = 1.0;   // case left beyond the board's screw holes and over its pins
// The longest screw that holds the board without reaching the case's surface
function trackball_screw_max(t) = 1.6 + trackball_gap_for(t) + t - trackball_wall_left;

// The housing's outline seen from above, grown by `grow` all round.
module trackball_ring_outline(grow = 0) {
    a = trackball_housing / 2;
    c = trackball_housing_cut;
    offset(delta = grow) polygon([
        [a[0] - c, -a[1]], [a[0], -a[1] + c], [a[0], a[1] - c], [a[0] - c, a[1]],
        [-a[0] + c, a[1]], [-a[0], a[1] - c], [-a[0], -a[1] + c], [-a[0] + c, -a[1]]]);
}

// The trackball's cut, centered on the ball, the panel's outer surface at
// z = 0 (w = 0 on the front). t is the panel's thickness there.
// `up`: when the hole prints sideways, the direction that faces up on the printer;
// the round hole then gets a small flat top 0.3 mm above the circle. The housing's
// pocket needs nothing: its flat top is a short bridge.
module trackball_cut(t = front_t, up = [0, 0]) {
    hole = trackball_lip_d + 0.5;
    translate([0, 0, -t - 1])
        if (up == [0, 0]) cylinder(d = hole, h = t + 2, $fn = 64);
        else sideways_hole(hole, t + 2, up, $fn = 64);
    translate([0, 0, -0.4]) cylinder(d1 = hole, d2 = hole + 0.8, h = 0.4 + eps, $fn = 64);  // soft edge
    translate([0, 0, -t - eps]) linear_extrude(t - trackball_skin + eps) trackball_ring_outline(trackball_play / 2);
}

// Cut from the inside of the panel: pilot holes for the board's screws, reaching
// up through the bosses into the panel, a shallow pocket over the module's base and
// tabs (only the housing has to fit its snug pocket), and a shallow groove over the row
// of pins, so their solder joints clear the case with the board this close.
// `up` as for trackball_cut().
module trackball_inside_cuts(t = front_t, up = [0, 0]) {
    gap = trackball_gap_for(t);
    translate([-trackball_base[0] / 2, -trackball_base[1] / 2, -t - eps])
        cube([trackball_base[0], trackball_base[1], t - trackball_wall_left + eps]);
    for (h = trackball_holes)
        translate([h[0], h[1], -t - gap - 1]) pilot_hole(m25_pilot, gap + 1 + t - trackball_wall_left, up);
    translate([-trackball_ball_at[0] - 0.5, trackball_pins_y - 1.6, -t - eps])
        cube([trackball_board[0] + 1, 3.2, t - 1.4 + eps]);
}

// Short bosses that hold the board at the right distance (M2.5 screws tap in).
module trackball_bosses(t = front_t) {
    gap = trackball_gap_for(t);
    if (gap > 0)
        for (h = trackball_holes)
            translate([h[0], h[1], -t - gap]) cylinder(d = 6, h = gap + eps);
}

// Stand-ins for the ball, its housing and its board, in the same frame.
module trackball_parts(t = front_t) {
    gap = trackball_gap_for(t);
    color("white") translate([0, 0, trackball_proud - trackball_ball_d / 2]) sphere(d = trackball_ball_d);
    color("#1d5fb0") translate([-trackball_ball_at[0], -trackball_ball_at[1], -t - gap - 1.6]) difference() {
        cube([trackball_board[0], trackball_board[1], 1.6]);
        for (h = trackball_holes)
            translate([h[0] + trackball_ball_at[0], h[1] + trackball_ball_at[1], -1]) cylinder(d = 2.9, h = 4, $fn = 24);
    }
    color("#222") translate([-trackball_base[0] / 2 + 0.6, -trackball_base[1] / 2 + 0.6, -t - gap])
        cube([trackball_base[0] - 1.2, trackball_base[1] - 1.2, trackball_base_h]);   // base and tabs
    color("#d8d8d8") translate([0, 0, -t - gap]) {
        linear_extrude(trackball_housing_h) difference() {
            trackball_ring_outline();
            circle(d = trackball_lip_d, $fn = 48);
        }
        linear_extrude(trackball_housing_h + 0.3) difference() {   // the lip
            circle(d = trackball_lip_d, $fn = 48);
            circle(d = trackball_ball_d + 0.4, $fn = 48);
        }
    }
    // the pins' solder joints along the top edge
    color("#c9a227") for (i = [0 : 10])
        translate([-trackball_ball_at[0] + 1.1 + i * 2.54, trackball_pins_y, -t - gap]) cylinder(d = 1.6, h = 1, $fn = 12);
}

// On the top, the ball's frame: its board turned a quarter turn, so the long
// side runs front to back beside the speaker.
module top_ball_frame() {
    translate([ball_top[0], ball_top[1], H]) rotate([0, 0, 90]) children();
}

// The clips that hold the screen: screwed to the collar's bosses with M2.5
// screws. The LCD's metal back rim sits lower in the pocket than the collar's top,
// so a nub under each clip's tip reaches down onto it, 0.8 mm inside the LCD's
// edge. The tip stays about 1 mm outside the smaller board behind the LCD, so the
// clips never touch the board or the parts on its back. z = 0 is the face that
// rests on the boss; the nub hangs below it.
clip_reach = clip_r - (face_glass_d / 2 - 0.8);   // screw to nub
module face_clip() {
    difference() {
        union() {
            hull() {
                cylinder(d = 6, h = face_clip_t);
                translate([-clip_reach, 0, 0]) cylinder(d = 3.5, h = face_clip_t);
            }
            if (face_clip_nub > 0)
                translate([-clip_reach, 0, -face_clip_nub]) cylinder(d = 3, h = face_clip_nub + eps, $fn = 32);
        }
        translate([0, 0, -1]) cylinder(d = m25_clear, h = face_clip_t + 2);
    }
}

// A clip as it prints: its flat back on the bed, the nub on top.
module face_clip_printed() {
    translate([0, 0, face_clip_t]) mirror([0, 0, 1]) face_clip();
}

// ---------------------------------------------------------------- back panel

module back_panel_flat() {
    usb_u = width - 28;
    usb_v = 20;
    difference() {
        intersection() {
            cube([width, H, back_t]);
            model_to_back() rounded_solid();
        }
        model_to_back() reveal_cuts();
        // (outer face is z = 0 here; it prints face up, so the rounded edges are on top)
        // (the screws' pan heads sit on the outside: the panel's too thin to sink them)
        for (p = back_post_spots()) translate([p[0], p[1], -1]) cylinder(d = m25_clear, h = back_t + 2);
        translate([usb_u, usb_v, 0]) usbc_cut(back_t);
        // Vent slots high up, where warm air collects
        for (i = [0 : 6]) translate([width / 2 - 33 + i * 11, H - 30, -1]) hull() {
            cylinder(d = 3, h = back_t + 2, $fn = 24);
            translate([0, 14, 0]) cylinder(d = 3, h = back_t + 2, $fn = 24);
        }
    }
}

// The USB-C socket's cut, centered on the socket, the panel's outer face at z = 0
// and its inside at z = t. The plate drops into a pocket from the inside, leaving
// usbc_skin in front of it, so the plug goes all the way in, and is glued there.
// A window as tall as the pocket lets the plug's body in too; with no thin ledges
// above and below it, the skin over the pocket's ends is a short bridge when the
// panel prints face up.
module usbc_cut(t = back_t) {
    pocket = usbc_plate + [0.4, 0.4];
    translate([-pocket[0] / 2, -pocket[1] / 2, usbc_skin]) cube([pocket[0], pocket[1], t]);
    translate([0, 0, -1]) hull() for (s = [-1, 1])
        translate([s * (usbc_window_w - pocket[1]) / 2, 0, 0]) cylinder(d = pocket[1], h = t + 2, $fn = 48);
}

// ---------------------------------------------------------------- test prints

// A strip with one of each hole: knob, trackball, USB-C, a nut slot, M2.5.
// Print it first (about 15 minutes) and test the parts in it.
module test_holes() {
    difference() {
        union() {
            cube([96, 22, front_t]);
            translate([79, 6, 0]) cube([10, 10, front_t + 2]);   // a back post's nut slot
            if (ball_on_top) {
                translate([19, 22 - wall, 0]) cube([71, wall, front_t + 16]);
                translate([75, 22 - foot_z0, 0]) cube([12, foot_z0, front_t + 16]);   // floor-thick, for the floor screws' hole
            }
        }
        translate([11, 11, -1]) cylinder(d = knob_hole_d, h = front_t + 2);
        // The trackball's hole: printed sideways in an upright tab when the ball is on top,
        // as it is in the body (which prints standing on its back); flat otherwise.
        if (ball_on_top) {
            translate([28, 22, front_t + 8]) rotate([-90, 0, 0]) trackball_cut(wall, up = [0, -1]);
            // and eight of the grille's hexagonal holes, printed sideways like the body's
            for (k = [0 : 3], row = [0 : (k % 2 == 0 ? 2 : 1)])
                translate([41 + k * 4.6 * 0.866, 23, front_t + 4 + row * 4.6 + (k % 2) * 2.3])
                    rotate([90, 0, 0]) grille_hole(wall + 2);   // a flat side faces up, as in the body
            // then the body's round holes that print sideways, left to right: a plain 3 mm
            // hole (to compare with the hexagons), the 2.2 mm pilot for M2.5 screws (speaker,
            // trackball board, face clips), the 2.8 mm Pi standoff hole, and the 2.8 mm hole
            // with its counterbore through floor-thick plastic (the front panel's screws)
            for (h = [[59, 3], [71, m25_clear], [81, m25_clear]])
                translate([h[0], 23, front_t + 8]) rotate([90, 0, 0]) cylinder(d = h[1], h = 7, $fn = 32);
            translate([65, 23, front_t + 8]) rotate([90, 0, 0]) pilot_hole(m25_pilot, 7, [0, 1]);
            translate([81, 22 + 1, front_t + 8]) rotate([90, 0, 0]) cylinder(d = m25_head_d, h = 1 + m25_head_h, $fn = 32);
        }
        else translate([28, 11, 0]) rotate([180, 0, 0]) trackball_cut();   // face down, like the front panel
        // the USB-C socket's pocket, face up like the back panel (so the pocket is underneath)
        translate([52, 11, front_t]) mirror([0, 0, 1]) usbc_cut(front_t);
        // the nut slot as in a back post, whose face prints on the bed: the screw comes up from below
        translate([84, 11, 0]) {
            translate([0, 0, -1]) cylinder(d = m25_clear, h = front_t + 4);
            translate([0, 0, nut_roof]) nut_slot(10);
        }
        translate([70, 11, -1]) cylinder(d = m25_clear, h = front_t + 2);
    }
}

// The face mount on its own: a ring with the window, LCD pocket, collar and
// clip bosses. Print it, drop the screen in, and check the fit before
// printing the whole front.
module test_face() {
    intersection() {
        // wide enough to keep a rim round the bosses and the collar at the notch's corners
        translate([width / 2, face_v, -front_t - collar_h - 1])
            cylinder(r = max(clip_r + 4.5, norm([face_tail_w / 2, face_glass_d / 2 + face_tail]) + collar_wall + 1.5), h = front_t + collar_h + 2);
        front_panel_local();
    }
}

// The trackball's patch of the top on its own: the ball's hole, the housing's pocket,
// the bosses and the clearance cuts, in an upright tab that prints standing up just
// as the body does (the holes sideways), with a foot to stand on. Screw the board on
// and check the housing sits flat against the skin and the ball turns freely.
module test_ball() {
    t = ball_on_top ? wall : front_t;
    x0 = -trackball_ball_at[0] - 3;                     // the tab's top edge, as printed
    x1 = trackball_board[0] - trackball_ball_at[0] + 2; // and its bottom, on the foot
    y0 = -trackball_ball_at[1] - 2;
    yl = trackball_board[1] + 4;
    translate([0, 0, x1 + 3]) rotate([0, 90, 0]) difference() {
        union() {
            translate([x0, y0, -t]) cube([x1 + 3 - x0, yl, t]);
            translate([x1, y0, -t - 12]) cube([3, yl, t + 16]);   // the foot
            trackball_bosses(t);
        }
        trackball_cut(t, up = [-1, 0]);
        trackball_inside_cuts(t, up = [-1, 0]);
    }
}

// The USB-C socket's patch of the back panel on its own: a small tile as thick as
// the panel, printed face up as the panel is (so the pocket is underneath). Push
// the plate in from below and plug the power supply in (glue it only in the real panel).
module test_usbc() {
    tile = usbc_plate + [8, 8];
    difference() {
        translate([-tile[0] / 2, -tile[1] / 2, 0]) cube([tile[0], tile[1], back_t]);
        translate([0, 0, back_t]) mirror([0, 0, 1]) usbc_cut(back_t);
    }
}

// Every kind of screw fixing in the case, each printed the way the case prints it,
// to try the kit's nylon screws and nuts in. Front row, left to right:
//  - a back post, face down, with its nut slot, and a back-panel-thick tab to
//    screw through it from below;
//  - a front panel foot, standing up with its nut slot open at the top, and an
//    upright floor-thick tab with the floor's counterbored hole (left) and the
//    Pi's (right), to screw up into the foot and to see the heads sit flush.
// Back rows: the pilot holes that the screws cut their own thread into, at 2.0,
// 2.1, 2.2 and 2.3 mm (the case uses m25_pilot), in a bar as thick as the front
// panel at a clip boss (holes opening upward, 0.8 mm short of the bottom), with a
// clip to screw through, and in an upright tab as the speaker's and trackball's
// bosses print (holes sideways).
module test_screws() {
    pilots = [2.0, 2.1, 2.2, 2.3];
    sp = 5;   // pilot holes' spacing
    // back post, its face on the bed
    difference() {
        cube([post, post, nut_roof + m25_nut_t + 0.3 + 2]);
        translate([post / 2, post / 2, -1]) cylinder(d = m25_clear, h = 1 + nut_roof + m25_nut_t + 0.3 + 1.5, $fn = 32);
        translate([post / 2, post / 2, nut_roof]) nut_slot(post);
    }
    translate([0, post + 2, 0]) difference() {   // the back panel
        cube([8, 8, back_t]);
        translate([4, 4, -1]) cylinder(d = m25_clear, h = back_t + 2, $fn = 32);
    }
    // a foot: the foot's bottom faces -y, its back end is on top (narrower and
    // shorter than the real one, with just enough round the nut)
    translate([post + 2, 0, 0]) difference() {
        cube([8, 7, 8]);
        translate([4, -eps, 4]) rotate([-90, 0, 0]) {
            sideways_hole(m25_clear, 1.5 + m25_nut_t + 0.3 + 1.5, [0, -1], clear = m25_clear / 2 * (sqrt(2) - 1), $fn = 32);
            translate([0, 0, 1.5]) rotate([0, 0, -90]) nut_slot(10);
        }
    }
    // the floor (foot_z0 thick) standing up, on a small base
    translate([post + 12, 0, 0]) difference() {
        union() {
            cube([16, foot_z0, 9]);
            translate([0, -2, 0]) cube([16, foot_z0 + 4, 1.2]);
        }
        for (h = [[4, m25_head_d, m25_head_h], [12, 5.2, 1.4]])   // the feet's, then the Pi's (as in pi_holes_cut)
            translate([h[0], -1, 5]) rotate([-90, 0, 0]) {
                cylinder(d = m25_clear, h = foot_z0 + 2, $fn = 32);
                cylinder(d = h[1], h = 1 + h[2], $fn = 32);
            }
    }
    // pilot holes, opening upward, in a bar as thick as the panel at a clip boss
    translate([10, post + 2, 0]) difference() {
        cube([4 * sp + 1, 6, front_t + collar_h]);
        for (i = [0 : 3]) translate([3 + i * sp, 3, 0.8]) cylinder(d = pilots[i], h = front_t + collar_h, $fn = 32);
        translate([-eps, -eps, front_t + collar_h - 1]) rotate([0, 0, -45]) cube([1.5, 1.5, 2]);  // a notch at the 2.0 mm end
    }
    translate([40, post + 5, 0]) face_clip_printed();
    // and sideways, as the body's bosses print, in an upright tab on a small base
    translate([10, post + 12, 0]) difference() {
        union() {
            cube([4 * sp + 1, 6.5, 7.5]);
            translate([0, -2, 0]) cube([4 * sp + 1, 10.5, 1.2]);
        }
        for (i = [0 : 3]) translate([3 + i * sp, -1, 4.5]) rotate([-90, 0, 0]) pilot_hole(pilots[i], 1 + 5.6, [0, -1]);
        translate([-eps, -eps, 7.5 - 1]) rotate([0, 0, -45]) cube([1.5, 1.5, 2]);
    }
}

// ---------------------------------------------------------------- output

module assembly() {
    color("#2b2f36") body();
    color("#16181c") front_panel_model();
    color("#3a3f48") translate([0, depth, 0]) rotate([90, 0, 0]) back_panel_flat();
    // Stand-ins for the parts, so you can see how it all fits.
    front_frame() {
        // The screen: LCD, the smaller board behind it, and the clips
        color("black") translate([width / 2, face_v, -bezel - face_lcd_t]) cylinder(d = face_glass_d, h = face_lcd_t);
        color("#0a2540") translate([width / 2, face_v, -bezel - face_lcd_t - 1.6]) cylinder(d = face_pcb_d, h = 1.6);
        color("#d0d0d0") for (a = face_clip_angles)
            translate([width / 2 + cos(a) * clip_r, face_v + sin(a) * clip_r, -front_t - collar_h])
                rotate([0, 0, a]) mirror([0, 0, 1]) face_clip();
        for (s = [-1, 1]) translate([width / 2 + s * knob_spacing, controls_v, 0]) {
            color("#1c1c1c") cylinder(d = 20, h = 9.6);          // black knob
            color("#c8c8c8") translate([0, 0, 9.6]) cylinder(d = 17, h = 0.4);  // brushed top
        }
        if (!ball_on_top) translate([width / 2, controls_v, 0]) trackball_parts();
        // Encoders behind the knobs: metal body, then the pins
        for (s = [-1, 1]) translate([width / 2 + s * knob_spacing, controls_v, -front_t]) {
            color("#9a9a9a") translate([-6.2, -6.6, -7]) cube([12.4, 13.2, 7]);
            color("#c9a227") for (x = [-2.5, 0, 2.5]) translate([x, -5, -10.5]) cylinder(d = 0.8, h = 3.5, $fn = 8);
        }
        // The tallest parts on the board's back
        color("#888") translate([width / 2 - 15, face_v - 10, -face_stack])
            cube([30, 20, face_stack - bezel - face_lcd_t - 1.6]);
    }
    if (ball_on_top) top_ball_frame() trackball_parts(wall);
    // Speaker: the cone under the grille, magnet below
    translate([width / 2, speaker_y, H - wall - speaker_boss_h]) {
        color("#555") translate([-speaker_frame / 2, -speaker_frame / 2, -1.5]) cube([speaker_frame, speaker_frame, 1.5]);
        color("#444") translate([0, 0, -12]) cylinder(d1 = 24, d2 = speaker_cone, h = 10.5);
        color("#333") translate([0, 0, -speaker_depth]) cylinder(d = speaker_magnet, h = speaker_depth - 12);
    }
    // Raspberry Pi Zero 2 W on its standoffs
    color("green") translate([pi_origin[0] - 3.5, pi_origin[1] - 3.5, wall + 5]) cube([65, 30, 1.6]);
    color("#222") translate([pi_origin[0] - 3.5 + 7, pi_origin[1] + 23 + 1, wall + 5 + 1.6]) cube([51, 5, 8.5]);  // GPIO header
    // MAX98357A amp, on the floor near the back
    color("#6a2c91") translate([width - wall - 24, depth - back_t - 21, wall]) cube([18, 19, 3]);
    // USB-C power socket, its plate in the back panel's pocket
    color("#2a2a2e") translate([width - 28 - usbc_plate[0] / 2, depth - usbc_skin - usbc_plate_t, 20 - usbc_plate[1] / 2])
        cube([usbc_plate[0], usbc_plate_t, usbc_plate[1]]);
    color("#c9ccd1") translate([width - 28 - 4.5, depth - usbc_skin - usbc_plate_t - 7.3, 20 - 1.6]) cube([9, 7.3, 3.2]);
}

// The case with its left side taken off, to see everything inside.
module inside_view() {
    difference() {
        assembly();
        translate([-1, -1, -1]) cube([wall + 1 + eps, depth + 2, H + 2]);
    }
}

if (part == "assembly") assembly();
else if (part == "inside") inside_view();
else if (part == "cutaway") intersection() {   // the left half, to see inside
    assembly();
    translate([-50, -50, -50]) cube([width / 2 + 50, depth + 100, H + 100]);
}
else if (part == "body") translate([0, 0, depth - back_t]) rotate([-90, 0, 0]) body();   // stands on its open back
else if (part == "front") translate([width, 0, 0]) rotate([0, 180, 0]) front_panel_local(); // face down
else if (part == "back") translate([0, H, back_t]) rotate([180, 0, 0]) back_panel_flat();   // face up
else if (part == "face_clip") for (i = [0 : len(face_clip_angles) - 1]) translate([i * 16, 0, 0]) face_clip_printed();
else if (part == "test_holes") test_holes();
else if (part == "test_face") translate([width, 0, 0]) rotate([0, 180, 0]) test_face();
else if (part == "test_ball") test_ball();
else if (part == "test_usbc") test_usbc();
else if (part == "test_screws") test_screws();

// Size check against the printer bed
echo(str("Radio size: ", width, " wide x ", depth, " deep x ", round(H), " tall (mm)"));
echo(str("Trackball ball sticks out ", trackball_proud, " mm; board sits ", trackball_gap,
          " mm behind the panel; its screws can be up to ", trackball_screw_max(ball_on_top ? wall : front_t), " mm long"));
if (trackball_base_h > trackball_gap + (ball_on_top ? wall : front_t) - trackball_wall_left)
    echo("WARNING: the trackball's base is taller than the pocket over it");
if (trackball_proud < 0.5) echo("WARNING: the ball barely sticks out: make trackball_skin thinner");
if (width > bed || depth > bed || H > bed) echo("WARNING: the body is bigger than the bed");
