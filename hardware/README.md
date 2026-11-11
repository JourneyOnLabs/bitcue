# BitCue hardware notes

What to buy, with links and an estimated total, is in [Get the hardware](../docs/buy.md). This page has the
details behind it: which screens the installer supports, and how the housing fits on the Z1.

## Versions

| Screen | Raspberry Pi | Housing | Installer option |
|---|---|---|---|
| Waveshare 3.5inch DPI LCD (SKU 19173) | Pi 3A+, 3B+, 3B or 4 | One housing for all four | `--dpi35` |
| Adafruit PiTFT Plus 2.8" (2298 or 2423) | Pi 4 | No housing | none (the default) |

Any Pi with Wi-Fi and the 40-pin header runs the software, a Zero 2 W for example, but the housing is made
only for the Pis above. A Pi 3 needs a 5 V 2.5 A micro-USB supply, a Pi 4 a 5 V 3 A USB-C one.

## Screens

- **Waveshare 3.5inch DPI LCD (SKU 19173):** 640 x 480 IPS, capacitive touch. Plugs onto the 40-pin header.
  5 V, 130 mA. BitCue draws its 320 x 240 layout at exactly 2x.
- **Adafruit PiTFT Plus 2.8" (2298 resistive, 2423 capacitive):** the original screen, drawn at 320 x 240.
  The installer's default. Often out of stock at Adafruit.
- **Waveshare 2.8inch DPI LCD (480 x 640, portrait):** also drawn at 2x. Not supported by the installer yet.
  Waveshare documents a fix needed on the Zero 2 W (grays show green).
- **HDMI monitor:** `--hdmi`. Uses a browser, so a Pi 4 or 5 is better than a Zero.

## Install

Write Raspberry Pi OS Lite (64-bit) to the card, then follow [Install](../README.md#install). For the
Waveshare screen add `--dpi35`:

```
sudo bash z1screen/install/install.sh --dpi35
```

The installer downloads Waveshare's two overlay files. If the Pi can't reach Waveshare's site, download
[3.5DPI-dtbo.zip](https://files.waveshare.com/wiki/3.5inch%20DPI%20LCD/3.5DPI-dtbo.zip) on a computer and put it in
the `z1screen` folder first.

### Mounted upside down

A housing mounted upside down, with the power cord coming out of the bottom, needs `--flip`:

```
sudo bash z1screen/install/install.sh --dpi35 --flip
```

This works the same on the PiTFT (no `--dpi35`). It turns on the settings page's switch "Turn the picture upside
down". BitCue then turns the picture 180° before writing it to the screen, from the splash at power-on, and turns
the touches to match.
Nothing changes in `config.txt` or `cmdline.txt` and no restart is needed. A touch setup done the other way up
keeps working. `--no-flip`, or the switch, turns it back. Without either option, the installer keeps the earlier
choice. The switch is kept in `/var/lib/z1screen/screen.json`, which the uninstaller leaves in place like the page's
other choices.

With a small screen the installer keeps the Pi's own boot messages off the screen, so BitCue's splash is the first
thing that shows after power on.

Why BitCue turns the picture itself instead of using the Pi's display rotation:

- On the 3.5inch DPI screen, Waveshare's wiki turns the picture with `video=DPI-1:640x480M@60,rotate=180` in
  `cmdline.txt`. It turns the touch with a libinput calibration matrix, which only desktops and browsers read.
  BitCue reads the touch chip itself, so that matrix does nothing for it.
- The kernel does turn the picture by 180° in the display hardware for that `rotate=` setting, when the display
  plane can do it, and the Pi's (vc4) planes can. BitCue's turn gives the same picture without editing the
  one-line `cmdline.txt`, and it keeps the settings page's switch and the touch setup in step with the picture.
- On the PiTFT, `rotate=270` in its overlay would turn the picture. The touch chip's readings would not change,
  so the saved touch setup would be wrong until it was done again.

Sources: [Waveshare wiki, 3.5inch DPI LCD](https://www.waveshare.com/wiki/3.5inch_DPI_LCD) (Display rotation and
Touch rotation sections); Linux
[drm_client_modeset.c](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_client_modeset.c)
(`drm_client_rotation`, 0° and 180° only, in the plane's hardware); Raspberry Pi's
[vc4_plane.c](https://github.com/raspberrypi/linux/blob/rpi-6.6.y/drivers/gpu/drm/vc4/vc4_plane.c) (planes offer 0°,
180° and mirroring).

## Housing

The housing is sold separately as 3D files ([Get the hardware](../docs/buy.md)). They are not in this
repository.

- A face plate that snaps onto a shell with four hooks, and a tilt plate held to the Z1's dark front base with
  industrial double-sided tape, 3M VHB 4910 or 5952: three strips of 1/2 in tape, or two of 3/4 in, across the
  plate's back. The Z1's enclosure is plastic, so magnets won't hold,
  and the thin tape that comes with the Z1 let go within an hour in testing. Stick the plate high on the base,
  within about 10 mm of its top edge: the face curves back near the bottom, and the plate has to lie flat.
  Clean the spot with isopropyl alcohol, press for 30 seconds and wait 24 hours before hanging the housing.
- The housing pivots on the plate and clicks at 0, 10, 20 or 30 degrees. It goes on and comes off only when
  held straight out.
- It hangs upside down so the power cord leaves at the bottom and runs under the machine. Install with
  `--flip`, or turn on **Turn the picture upside down** on the settings page; it turns the touches too
  ([Mounted upside down](#mounted-upside-down)).
- No screws: the Pi sits on four locating pins, the screen plugs onto its header, and the face plate holds the
  stack. In the universal version, small ledges in the shell sit just behind the screen board's edge, so the
  screen doesn't rock when pressed.
- A stick-on heatsink, 14 x 14 mm and 8 to 10 mm tall, goes on the Pi's processor. No fan is needed; print the
  shell in PETG for a Pi 3B+ or 4.
- Print the tilt plate in PLA-CF or PETG. Its spring arms bend hard at every click, and plain PLA may crack at
  their roots.

Sources: [Waveshare wiki, 3.5inch DPI LCD](https://www.waveshare.com/wiki/3.5inch_DPI_LCD),
[Raspberry Pi product drawings](https://datasheets.raspberrypi.com/),
[Adafruit PiTFT Plus 2.8"](https://www.adafruit.com/product/2298).

### Table stand

The universal version also comes with a one-piece table stand, for the desk next to the Z1:

- The tilt plate's ears, slots and click, on a face panel leaning back 15 degrees, with two side fins and a
  140 x 102 mm base. Four stick-on rubber feet (8 to 12 mm across, 2 to 3 mm tall) go under the base; no
  tape.
- The housing hangs on it the same way as on the Z1, so the install is the same (`--flip`). The four clicks
  lean the screen back 15, 25, 35 or 45 degrees.
- The cord curves down onto the base and leaves through an arch at the bottom of the face panel.
- Print it standing on its base, in PLA-CF or PETG, without supports: about 2.5 hours and 93 g at 0.2 mm
  layers.
- Hold the base when you change the angle. At the 15-degree click a hard press near the top of the screen
  can tip it back; the steeper clicks are steadier.
- Printed and tested with a Pi 3B+ and the Waveshare screen (November 2026).
