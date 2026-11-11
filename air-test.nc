; BitCue test: three tool changes (T2, T3, T1), no cutting.
; The spindle stays off. All moves happen 10 mm below the top of the Z travel.
; Run window: work origin Anchor 1, offsets 0. Switch OFF Auto Z Probe,
; Auto Leveling and Scan Margin for this test.
; At each stop, fit the bit the screen asks for and press the front button once.
G90 G21 G17
G54
G53 G0 Z-10
T2 M6
G53 G0 Z-10
G0 X20 Y20
G4 S20
G0 X60 Y20
G4 S20
T3 M6
G53 G0 Z-10
G0 X20 Y60
G4 S20
G0 X60 Y60
G4 S20
T1 M6
G53 G0 Z-10
G0 X40 Y40
G4 S20
G53 G0 Z-10
M30
