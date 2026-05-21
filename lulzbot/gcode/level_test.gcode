; Lulzbot Mini start-gcode for TPU, distilled from CuraLE log.
; Heats, retracts filament 30mm (TPU anti-ooze), wipes nozzle,
; probes 4 corners with G29, then stops. No actual print.

G21                                ; units = mm
M107                               ; fans off
G90                                ; absolute positioning
M82                                ; extruder absolute mode
G92 E0                             ; reset extruder pos
M140 S60                           ; start bed heating
M109 R180                          ; heat extruder to wipe temp
G28                                ; home all axes
G0 X0 Y187 Z156 F200               ; move away from endstops
G1 E-30 F75                        ; retract filament 30mm (TPU anti-ooze)
G1 X42 Y173 F11520                 ; move above wiper pad
G1 Z0 F1200                        ; push nozzle into wiper
G1 X42 Y173 Z-.5 F4000             ; wiping
G1 X52 Y171 Z-.5 F4000             ; wiping
G1 X42 Y173 Z0 F4000               ; wiping
G1 X52 Y171 F4000                  ; wiping
G1 X42 Y173 F4000                  ; wiping
G1 X52 Y171 F4000                  ; wiping
G1 X57 Y173 F4000                  ; wiping
G1 X77 Y171 F4000                  ; wiping
G1 X57 Y173 F4000                  ; wiping
G1 X77 Y171 F4000                  ; wiping
G1 X87 Y171 F4000                  ; wiping
G1 X77 Y173 F4000                  ; wiping
G1 X97 Y171 F4000                  ; wiping
G1 X77 Y173 F4000                  ; wiping
G1 X107 Y173 F4000                 ; wiping
G1 X97 Y171 F4000                  ; wiping
G1 X112 Y171 Z-0.5 F1000           ; wiping
G1 Z10                             ; raise extruder
G28 X0 Y0                          ; re-home XY
G0 X0 Y187 F200                    ; move away from endstops
M109 R160                          ; cool extruder to probe temp
M204 S300                          ; set probing acceleration
G29                                ; AUTO-BED-LEVEL (the failing step)
M420 S1                            ; enable leveling matrix
M400                               ; wait for everything to finish
M104 S0                            ; turn off hotend
M140 S0                            ; turn off bed
M84                                ; disable steppers
