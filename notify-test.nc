; BitCue notification test: five bit changes (T2, T6, T3, T4, T1) about 1.5 minutes apart, no cutting.
; The spindle never turns on. All moves happen 10 mm below the top of the Z travel, over the
; Anchor 1 corner (X20-60 Y20-60). Slow air moves (not pauses) fill the time, so the screen's
; countdown and the early warning work as in a real job. About 7 minutes, plus the time of each change.
; Without the Z1: python3 tools/notify_test.py plays it on a pretend Z1 (see that file).
; Run window: work origin Anchor 1, offsets 0. Switch OFF Auto Z Probe, Auto Leveling and Scan Margin.
; At each stop you can fit the bit the screen asks for, or leave the bit in and just press the front
; button once. The Z1 skips a stop for the bit already in the spindle, so start with T1 in the
; spindle. Connect Makera Studio through the _screen entry: the Pi only sees stops it can watch.
; Upload this file through that connection too: the Pi reads it on the way, which the countdown
; and the early warning need (the stop notifications work without it).
G90 G21 G17
G54
G53 G0 Z-10
T2 M6
G53 G0 Z-10
G0 X20 Y20
G1 X60 Y20 F300
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
G1 X60 Y20
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
G1 X60 Y20
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
T6 M6
G53 G0 Z-10
G0 X20 Y20
G1 X60 Y20 F300
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
G1 X60 Y20
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
G1 X60 Y20
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
T3 M6
G53 G0 Z-10
G0 X20 Y20
G1 X60 Y20 F300
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
G1 X60 Y20
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
G1 X60 Y20
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
T4 M6
G53 G0 Z-10
G0 X20 Y20
G1 X60 Y20 F300
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
G1 X60 Y20
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
G1 X60 Y20
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
T1 M6
G53 G0 Z-10
G0 X20 Y20
G1 X60 Y20 F300
G1 X60 Y60
G1 X20 Y60
G1 X20 Y20
G53 G0 Z-10
M30
