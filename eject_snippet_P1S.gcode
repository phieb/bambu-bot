; ===== Farmloop Auto-Eject (P1S) =====
; Bambuddy per-model END snippet. Paste into Settings -> G-code snippets ->
; model "P1S" -> end_gcode (see EJECT-SETUP.md). Bambuddy injects this just
; before "; EXECUTABLE_BLOCK_END" at dispatch and recomputes the plate .md5.
;
; {clamp(...)} is a Bambuddy placeholder: max_z_height is the print's top-layer Z
; (parsed from the 3MF header), so the sweep height tracks each job. Set the
; model's max_height_mm guard to 180 so a too-tall part is refused, not crashed.
;
; Only P1S-native commands (G0/G1/G28/G90/M17/M104/M106/M107/M109/M140).
; NO M84 — the P1S parser rejects a file that contains it (HMS 0500-4003).
M17 X0.8 Y0.8 Z0.5 ; 45% motor current (gentle)
G90
M104 S0            ; hotend heater off
M140 S0            ; bed heater off
M106 S255          ; part fan full — start cooling the nozzle NOW
; --- 1. Bender: flex the bed deep to release adhesion (nozzle cools meanwhile) ---
G0 Z240.0 F3000
G0 X20 Y240 F3500
G0 Z240.0 F3000
G0 Z200.0 F3000
G0 Z240.0 F3000
G0 Z200.0 F3000
G0 Z240.0 F3000
G0 Z200.0 F3000
G0 Z240.0 F3000
M109 R50           ; WAIT until nozzle <= 50C before any move at grab height
                   ; (a hot nozzle drags on the part body and leaves melt marks)
; --- 2. Sweep: centre lane first (square-on push), then fan outward ---
G0 Z{clamp(max_z_height - 4, 1.5, 176) + 30} F3000
G0 X128.00 Y250.0 F3500
G0 Z{clamp(max_z_height - 4, 1.5, 176)} F3000
G1 Y0.0 F3500
G0 Z{clamp(max_z_height - 4, 1.5, 176) + 30} F3000
G0 X98.00 Y250.0 F3500
G0 Z{clamp(max_z_height - 4, 1.5, 176)} F3000
G1 Y0.0 F3500
G0 Z{clamp(max_z_height - 4, 1.5, 176) + 30} F3000
G0 X158.00 Y250.0 F3500
G0 Z{clamp(max_z_height - 4, 1.5, 176)} F3000
G1 Y0.0 F3500
G0 Z{clamp(max_z_height - 4, 1.5, 176) + 30} F3000
G0 X50.00 Y250.0 F3500
G0 Z{clamp(max_z_height - 4, 1.5, 176)} F3000
G1 Y0.0 F3500
G0 Z{clamp(max_z_height - 4, 1.5, 176) + 30} F3000
G0 X206.00 Y250.0 F3500
G0 Z{clamp(max_z_height - 4, 1.5, 176)} F3000
G1 Y0.0 F3500
; --- Park + home for a clean reference next print ---
M107               ; part fan off
G0 Z200.0 F3000
G28 X
G28 Z
; ===== eject end (no M84 — the P1S does not use it) =====
