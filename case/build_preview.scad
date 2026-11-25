// World Desk Radio: the whole build, with every part and cable in place.
//
// Loads radio_case.scad and adds stand-ins for the electronics, at their real
// sizes and in their real places, to check that everything fits and to show how
// it goes together. Nothing here is printed.
//
// `show` picks what to draw (each one can be exported on its own as an STL):
//   "all"   the case and everything inside       "inside"  the same, left side cut away
//   "open_back"  everything but the back panel, to see in from behind
//   "case"  just the printed parts               "parts"   just the electronics and cables
//   or one group: "pi", "face", "controls", "trackball", "speaker", "amp", "usbc",
//   "power_cable", "face_cable", "wires_tune", "wires_volume", "wires_ball",
//   "wires_amp", "speaker_wires"

include <radio_case.scad>
part = "none";        // (keeps radio_case.scad from drawing its own preview)
show = "all";

// ---------------------------------------------------------------- Raspberry Pi Zero 2 W
// On four 5 mm standoffs on the floor pads. It's turned so its GPIO header runs
// along the front edge (short wires to the knobs and trackball) and its two
// micro-USB ports face the back (room for the plugs, and near the power socket).
pi_z      = wall + 1 + 5;                      // board's underside: pad top + standoff
pi_x0     = pi_origin[0] - 3.5;                // board corner
pi_y0     = pi_origin[1] - 3.5;
pi_top    = pi_z + 1.6;
pi_port_z = pi_top + 1.3;                      // micro-USB ports' middle
pi_usb_x  = pi_x0 + 65 - 41.4;                 // the port marked USB (to the face)
pi_pwr_x  = pi_x0 + 65 - 54;                   // the port marked PWR IN
hdr_x0    = pi_x0 + 7.1;                       // GPIO header, 2 x 20 pins
hdr_y     = pi_y0 + 3.5;                       // its middle, across

module pi() {
    // board
    color("#1f7a3a") difference() {
        translate([pi_x0, pi_y0, pi_z]) linear_extrude(1.6) offset(r = 3) offset(delta = -3) square([65, 30]);
        for (x = [0, pi_holes[0]], y = [0, pi_holes[1]])
            translate([pi_origin[0] + x, pi_origin[1] + y, pi_z - 1]) cylinder(d = 2.75, h = 4, $fn = 16);
    }
    // standoffs
    color("#e8e4d8") for (x = [0, pi_holes[0]], y = [0, pi_holes[1]])
        translate([pi_origin[0] + x, pi_origin[1] + y, wall + 1]) cylinder(d = 5, h = 5, $fn = 6);
    // processor, ports, card slot, camera connector
    color("#9aa0a6") translate([pi_x0 + 32.5 - 5.5, pi_y0 + 15 - 5.5, pi_top]) cube([11, 11, 1.2]);
    color("#c9ccd1") for (x = [pi_usb_x, pi_pwr_x])
        translate([x - 4, pi_y0 + 30 - 4.6, pi_top]) cube([8, 5.6, 2.6]);
    color("#c9ccd1") translate([pi_x0 + 65 - 12.4 - 5.6, pi_y0 + 30 - 6.6, pi_top]) cube([11.2, 7.6, 3.3]);
    color("#c9ccd1") translate([pi_x0 + 65 - 11, pi_y0 + 9.5, pi_z - 1.4]) cube([12, 11.5, 1.4]);
    color("#e7e2c8") translate([pi_x0 - 0.5, pi_y0 + 6.5, pi_top]) cube([4, 17, 1.2]);
    // GPIO header, with the jumper wires' plugs on it
    color("#202124") translate([hdr_x0, hdr_y - 2.54, pi_top]) cube([50.8, 5.08, 2.5]);
    color("#c9a227") for (i = [0 : 19], j = [0, 1])
        translate([hdr_x0 + 1.27 + i * 2.54 - 0.32, hdr_y - 1.27 + j * 2.54 - 0.32, pi_top + 2.5]) cube([0.64, 0.64, 3]);
    color("#2a2a2e") translate([hdr_x0, hdr_y - 2.54, pi_top + 5.5]) cube([50.8, 5.08, 12]);
}

// ---------------------------------------------------------------- the face (round screen)
// LCD, the smaller board behind it, its two USB-C sockets (seen from the front,
// UART at lower left, USB at lower right), the ribbon's fold, and the clips.
face_board_w = -bezel - face_lcd_t;                      // the board's front face (w)
module face() {
    front_frame() {
        color("#08090b") translate([width / 2, face_v, -bezel - face_lcd_t]) cylinder(d = face_glass_d, h = face_lcd_t);
        color("#1d4f8f") translate([width / 2, face_v, face_board_w - 1.6]) cylinder(d = face_pcb_d, h = 1.6);
        color("#2a2d33") translate([width / 2 - 15, face_v - 10, face_board_w - 1.6 - 3])
            cube([30, 20, 3]);                                      // chips on the back
        color("#c9ccd1") for (a = [228, 312])
            translate([width / 2 + cos(a) * 30, face_v + sin(a) * 30, face_board_w - 1.6 - 3.3])
                rotate([0, 0, a]) translate([-3.6, -4.6, 0]) cube([7.5, 9.2, 3.3]);
        color("#141414") translate([width / 2 - face_tail_w / 2 + 1, face_v - face_glass_d / 2 - 1.2, face_board_w - 1.6])
            cube([face_tail_w - 2, 1.2, face_lcd_t + 1.6]);         // ribbon fold
        color("#d0d0d0") for (a = face_clip_angles)
            translate([width / 2 + cos(a) * clip_r, face_v + sin(a) * clip_r, -front_t - collar_h])
                rotate([0, 0, a]) mirror([0, 0, 1]) face_clip();
    }
}

// ---------------------------------------------------------------- knobs and encoders
module controls() {
    front_frame() for (s = [-1, 1]) translate([width / 2 + s * knob_spacing, controls_v, 0]) {
        color("#1c1c1c") cylinder(d = 20, h = 9.6);                        // knob
        color("#c8c8c8") translate([0, 0, 9.6]) cylinder(d = 17, h = 0.4);  // its brushed top
        color("#b9bcc0") cylinder(d = 9.5, h = 2, $fn = 6);                  // nut
        color("#9a9a9a") translate([-6.2, -6.6, -front_t - 7]) cube([12.4, 13.2, 7]);   // body
        color("#c9a227") for (x = [-2.5, 0, 2.5]) translate([x, -5, -front_t - 10.5]) cylinder(d = 0.8, h = 3.5, $fn = 8);
        color("#c9a227") for (x = [-2.5, 2.5]) translate([x, 5, -front_t - 10.5]) cylinder(d = 0.8, h = 3.5, $fn = 8);
        // jumper plugs on its pins: three on one side, two on the other
        color("#2a2a2e") translate([-3.8, -6.3, -front_t - 24]) cube([7.6, 2.6, 14]);
        color("#2a2a2e") translate([-3.8, 3.7, -front_t - 24]) cube([7.6, 2.6, 14]);
    }
}

// ---------------------------------------------------------------- trackball module
// Under the top at the front right; its pin header runs along the edge nearer
// the middle, with the jumper plugs hanging down.
module trackball() {
    top_ball_frame() {
        trackball_parts(wall);
        g = trackball_gap_for(wall);
        hx = -trackball_ball_at[0] + 0.5;   // the header's 11 pins, from VCC to BLU
        color("#c9a227") translate([hx, trackball_pins_y - 0.32, -wall - g - 1.6 - 2.5]) cube([27, 0.64, 2.5]);
        color("#2a2a2e") translate([hx, trackball_pins_y - 1.3, -wall - g - 1.6 - 2.5 - 14]) cube([27, 2.6, 14]);
    }
}

// ---------------------------------------------------------------- speaker
speaker_z = H - wall - speaker_boss_h;          // the frame's top face, on the bosses
module speaker() {
    translate([width / 2, speaker_y, speaker_z]) {
        color("#3d4148") difference() {
            translate([-speaker_frame / 2, -speaker_frame / 2, -1.5]) cube([speaker_frame, speaker_frame, 1.5]);
            translate([0, 0, -2]) cylinder(d = speaker_cone, h = 3);
        }
        color("#585c63") translate([0, 0, -12]) cylinder(d1 = 24, d2 = speaker_cone, h = 10.5);
        color("#26282c") translate([0, 0, -speaker_depth]) cylinder(d = speaker_magnet, h = speaker_depth - 12);
        color("#c9a227") for (s = [-1, 1]) translate([22, s * 4 - 1, -3.5]) cube([3, 2, 2]);   // solder tabs
    }
}

// ---------------------------------------------------------------- amp (MAX98357A, pins soldered)
// Sits on its jumper plugs near the back right, its speaker terminal on top.
amp_x0 = width - wall - 6 - 17.8;
amp_y0 = 64;                                    // just behind the Pi
amp_z  = wall + 1 + 5 + 14;                     // board's underside, on its plugs
module amp() {
    color("#5b2a86") translate([amp_x0, amp_y0, amp_z]) cube([17.8, 19.3, 1.6]);
    color("#2a2d33") translate([amp_x0 + 6, amp_y0 + 7, amp_z + 1.6]) cube([3, 3, 0.9]);
    color("#2f8f4e") translate([amp_x0 + 4, amp_y0 + 11, amp_z + 1.6]) cube([10, 7.5, 10]);      // terminal
    color("#c9a227") translate([amp_x0, amp_y0 + 1, amp_z - 6]) cube([17.8, 0.64, 6]);         // pins
    color("#2a2a2e") translate([amp_x0, amp_y0, amp_z - 6 - 14 + 6]) cube([17.8, 2.6, 14]);    // plugs
}

// ---------------------------------------------------------------- USB-C power socket
usbc_u = width - 28;   // as in back_panel_flat()
module usbc() {
    py = depth - usbc_skin - usbc_plate_t;   // the plate's back, in its pocket
    color("#2a2a2e") translate([usbc_u - usbc_plate[0] / 2, py, 20 - usbc_plate[1] / 2]) cube([usbc_plate[0], usbc_plate_t, usbc_plate[1]]);
    color("#c9ccd1") translate([usbc_u - 4.5, py - 7.3, 20 - 1.6]) cube([9, 7.3, 3.2]);   // socket
    color("#2a6e3f") translate([usbc_u - 5, py - 7.3 - 6, 20 - 0.8]) cube([10, 7, 1.6]); // its small board
}

// ---------------------------------------------------------------- cables
// A cable as a tube through a list of points (centers), d thick.
module tube(pts, d) {
    for (i = [0 : len(pts) - 2]) hull() {
        translate(pts[i]) sphere(d = d, $fn = 12);
        translate(pts[i + 1]) sphere(d = d, $fn = 12);
    }
}

// Power: the cut micro-USB cable from the socket's VBUS/GND pads to the Pi's
// PWR IN port (a straight plug pointing back).
module power_cable() {
    color("#c9ccd1") translate([pi_pwr_x - 3.5, pi_y0 + 30 - 1, pi_port_z - 0.9]) cube([7, 6, 1.8]);
    color("#1b1b1b") translate([pi_pwr_x - 5.3, pi_y0 + 30 + 5, pi_port_z - 3.5]) cube([10.6, 13, 7]);
    color("#1b1b1b") tube([[pi_pwr_x, pi_y0 + 48, pi_port_z], [pi_pwr_x + 8, 76, pi_port_z + 1],
                           [62, 82, 16], [usbc_u - 2, depth - usbc_skin - usbc_plate_t - 12, 21]], 3.5);
}

// The face's link: an angled USB-C plug in the face's UART socket, the cable
// back into the case, and an angled micro-USB plug in the Pi's USB port.
face_plug_r = 40;
face_cable_end = let(u = width / 2 + cos(228) * (face_plug_r + 3.5), v = face_v + sin(228) * (face_plug_r + 3.5), w = -21)
    [u, v * sin(tilt) - w * cos(tilt), v * cos(tilt) + w * sin(tilt)];
module face_cable() {
    color("#1b1b1b") front_frame()
        translate([width / 2 + cos(228) * face_plug_r, face_v + sin(228) * face_plug_r, face_board_w - 1.6 - 1.6])
            rotate([0, 0, 228]) {
                translate([-5, -6, -3.25]) cube([10, 12, 6.5]);       // plug body, along the board
                translate([0, -5, -14.5]) cube([7, 10, 13]);           // its elbow, turning back
            }
    color("#c9ccd1") translate([pi_usb_x - 3.5, pi_y0 + 30 - 1, pi_port_z - 0.9]) cube([7, 6, 1.8]);
    color("#1b1b1b") translate([pi_usb_x - 4.8, pi_y0 + 30 + 5, pi_port_z - 3.5]) cube([9.6, 7, 7]);
    color("#1b1b1b") translate([pi_usb_x - 3.5, pi_y0 + 30 + 6, pi_port_z + 3.5]) cube([7, 6, 6]);
    color("#1b1b1b") tube([[pi_usb_x, pi_y0 + 39, pi_port_z + 9], [pi_usb_x - 1, 50, 34], [34, 36, 38], face_cable_end], 3.5);
}

// Jumper wires from the Pi's header to each part (colors as on the wiring sheet).
function hdr_top(x) = [x, hdr_y, pi_top + 17.5];
module wires_tune()   { color("#2c9a5b") tube([hdr_top(hdr_x0 + 4),  [24, 34, 24], [width / 2 - knob_spacing, 33, 14], [width / 2 - knob_spacing, 29, 9]], 3); }
module wires_volume() { color("#b88400") tube([hdr_top(hdr_x0 + 46), [84, 34, 24], [width / 2 + knob_spacing, 33, 14], [width / 2 + knob_spacing, 29, 9]], 3); }
module wires_ball()   { color("#8a4fc7") tube([hdr_top(hdr_x0 + 38), [96, 30, 33], [102, 38, 56], [100, 50, 84],
                                               [ball_top[0] - trackball_pins_y, ball_top[1], H - 23]], 3.5); }
// (over the top of the Pi and down past its right edge, clear of the Volume knob's plugs)
module wires_amp()    { color("#2f6fd0") tube([hdr_top(hdr_x0 + 30), [72, 44, 26], [88.5, 56, 20], [92, 62, 14],
                                               [92, 63, 6.5], [amp_x0 + 9, amp_y0 + 1.3, amp_z - 15]], 3); }
module speaker_wires() { color("#c0392b") tube([[amp_x0 + 9, amp_y0 + 15, amp_z + 11.6], [94, 77, 50], [86, 76, 88],
                                                [width / 2 + 23.5, speaker_y, speaker_z - 3.5]], 2); }

// ---------------------------------------------------------------- output
module case_parts() {
    color("#2b2f36") body();
    color("#16181c") front_panel_model();
    color("#3a3f48") translate([0, depth, 0]) rotate([90, 0, 0]) back_panel_flat();
}
module parts() {
    pi(); face(); controls(); trackball(); speaker(); amp(); usbc();
    power_cable(); face_cable(); wires_tune(); wires_volume(); wires_ball(); wires_amp(); speaker_wires();
}

if (show == "all") { case_parts(); parts(); }
else if (show == "inside") difference() {
    union() { case_parts(); parts(); }
    translate([-1, -1, -1]) cube([wall + 1 + eps, depth + 2, H + 2]);
}
else if (show == "open_back") {   // the back panel left off, to see in from behind
    color("#2b2f36") body(); color("#16181c") front_panel_model(); parts();
}
else if (show == "case") case_parts();
else if (show == "parts") parts();
else if (show == "pi") pi();
else if (show == "face") face();
else if (show == "controls") controls();
else if (show == "trackball") trackball();
else if (show == "speaker") speaker();
else if (show == "amp") amp();
else if (show == "usbc") usbc();
else if (show == "power_cable") power_cable();
else if (show == "face_cable") face_cable();
else if (show == "wires_tune") wires_tune();
else if (show == "wires_volume") wires_volume();
else if (show == "wires_ball") wires_ball();
else if (show == "wires_amp") wires_amp();
else if (show == "speaker_wires") speaker_wires();
