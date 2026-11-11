# Get the hardware

BitCue is free software. To put it on the front of your Z1, or on the desk beside it, you need a Raspberry
Pi, a touch screen and a housing:

| | What it is | Where |
|---|---|---|
| **[3D files](#3d-files)** | The housing, to print yourself or have printed: STL, 3MF and STEP, with a plate for the Z1 and a table stand for the desk. $8. | [Printables Store][printables] · [Cults3D][cults3d] |
| **[Electronics](#electronics-parts-list)** | The Pi, screen, card and power supply. Not sold by BitCue: buy them yourself from the parts list. | Parts list below |

The 3D files are sold by BitCue's author, who doesn't sell printed housings. Some links in the parts list are
affiliate links ([disclosure](#electronics-parts-list)).

*BitCue is an independent project, not made or endorsed by Makera.*

## What it fits

One housing fits a Raspberry Pi 3A+, 3B+, 3B or 4 with the Waveshare 3.5inch DPI LCD (640 x 480, capacitive
touch, SKU 19173). A Pi 3B+ is the easiest to buy: the Pi 3A+ is often out of stock.

The software also runs on other Pis with Wi-Fi and the 40-pin header (a Zero 2 W, for example), on Adafruit's
PiTFT Plus 2.8" and on an HDMI monitor, but there is no housing for those.

## The housing

The same housing goes on the Z1 or on the desk beside it:

- **On the Z1:** it hangs on a slim plate stuck to the Z1's dark front base with industrial double-sided tape
  (3M VHB), left or right of the front button. No drilling, no arm. It tilts to 0, 10, 20 or 30 degrees with a
  click, on top of the Z1's own 10-degree lean. The power cord leaves at the bottom and runs under the machine.
- **On the desk:** it hangs on a one-piece table stand, 140 x 102 mm, on stick-on rubber feet, with no tape. The
  same clicks lean the screen back 15, 25, 35 or 45 degrees. The power cord curves down onto the stand's base and
  leaves through an arch at the bottom.

Either way:

- It comes off only when held straight out.
- The face plate snaps onto the shell with four hooks. No screws hold the housing together.
- Fit-checked in CAD against Waveshare's 3D model and the official Raspberry Pi drawings, then printed and tested
  with a Pi 3B+ and the Waveshare screen, on a Z1 and on the table stand.

The housing hangs upside down, on the stand too. The installer asks about it and turns the picture to match;
the settings page's switch **Turn the picture upside down** (`http://z1screen.local:8080/settings`) does the
same.

## 3D files

**[Printables Store][printables]** · **[Cults3D][cults3d]** ($8)

- The face plate, the shell, the tilt plate for the Z1 and the table stand, with a one-plate print project and a
  CAD assembly for each set-up.
- STL and 3MF laid out in print orientation, and STEP to change in your own CAD.
- Prints without supports. Print the tilt plate and the table stand in PLA-CF or PETG. The README in the files
  has the filament, print settings and assembly steps.
- Print it in charcoal or black to match the Z1's dark base, or light gray to match its white sides.

No printer? Have it printed for you by an online 3D printing service, a local makerspace or library, or a
friend with a printer. That is fine under the licence below, as long as the prints are for you.

The 3D files are **not in this repository** and are not covered by its GPL licence. They are sold under the
store's personal-use licence: print them for yourself (or have them printed for you), but don't share the
files or sell prints.

## Electronics (parts list)

> **Disclosure:** some links below are affiliate links. If you buy through them, BitCue earns a small
> commission at no extra cost to you. As an Amazon Associate I earn from qualifying purchases.

Check the price on each seller's page. The total below is an estimate from US prices in November 2026, before
shipping and tax.

### One of each

Where a part gives a choice, get just one of them.

| You need | Get | Link |
|---|---|---|
| **Screen** | Waveshare 3.5inch DPI LCD, 640 x 480 IPS, capacitive touch | [Amazon][amazon-waveshare-35dpi] · [Waveshare][waveshare-35dpi] |
| **Raspberry Pi** (pick one) | Pi 3 Model B+ (the easiest to find; a 3 B works too) | [Amazon][amazon-pi-3b-plus] · [approved resellers][rpi-3b-plus] |
| | *or* Pi 4 Model B (2 GB is plenty) | [Amazon][amazon-pi-4] · [approved resellers][rpi-4] |
| | *or* Pi 3 Model A+ (often out of stock) | [approved resellers][rpi-3a-plus] |
| **Power supply** (the one for your Pi) | For a Pi 3: 5 V 2.5 A micro-USB (CanaKit) | [Amazon][amazon-psu-micro-usb] |
| | *or* for a Pi 4: 5 V 3 A USB-C (official Raspberry Pi) | [Amazon][amazon-psu-usb-c] |
| **microSD card** | 32 GB, A1 (SanDisk Ultra) | [Amazon][amazon-microsd] |
| **Heatsink** | Stick-on, for the Pi's processor, 14 x 14 mm, 8 to 10 mm tall | [Adafruit 3083][adafruit-3083] (not an affiliate link) |
| **Housing** | The 3D files ($8, plus printing) | [Printables Store][printables] · [Cults3D][cults3d] |

### For your set-up

Get the part for the set-up you'll use, or both if you want both.

| Set-up | You need | Link |
|---|---|---|
| **On the Z1** | Industrial double-sided tape, 3M VHB 4910 or 5952: three strips of 1/2 in tape, or two of 3/4 in, across the plate's back. One roll is plenty. | [Amazon][amazon-vhb-tape] |
| **On the desk** | 4 stick-on rubber feet, 8 to 12 mm across, for the table stand | [Amazon][amazon-rubber-feet] |

### Optional

| Part | Link |
|---|---|
| 90° adapter for the power plug, the same kind as your supply: micro-USB for a Pi 3, USB-C for a Pi 4. The supply's cord is fixed, and the adapter makes the turn under the housing easier. | [micro-USB][amazon-adapter-micro-usb] · [USB-C][amazon-adapter-usb-c] |
| 0.5 mm foam tape: a strip under the face plate's lip keeps the screen from rattling (recommended) | [Amazon][amazon-foam-tape] |
| Colored vinyl tape, 10 colors, to mark where each bit lives | [Tape Jungle][tape-10-color] (not an affiliate link) |

In total, about $170 with a Pi 3B+ and the 3D files, plus the filament or the print service. With a Pi 3A+ it
is about $15 less.

The housing with the Pi and screen weighs about 0.2 kg. By 3M's guideline for a constant load (4 square inches
of tape per pound), that much tape holds about three times that. Stick the plate on with the VHB tape, not the
thin double-sided tape that comes with the Z1: in testing, that let go within an hour. Clean the spot with
isopropyl alcohol, press the plate on hard for 30 seconds, and leave it for 24 hours before you hang the
housing on it.

Put the colored tape beside each bit's slot in the bit holder, not on the bit: tape on the shank or collar
can stop it seating. The Tape Jungle pack has every color BitCue uses except light blue and brown (T7 and
T8). Colored paper dots work too, but common packs (Avery 5795) have only red, yellow, green and blue.

Writing the card needs a microSD reader; a computer without a card slot needs a USB one.

## Then

1. Stick the heatsink on the Pi's processor, plug the screen onto the Pi's header, lay them in the shell and
   snap the face plate on. There are no screws. The README in the 3D files has the full steps.
2. On the Z1: stick the tilt plate high on the Z1's dark front base, within about 10 mm of its top, so it
   sits flat above the curve at the bottom. Hang the housing on it and clamp the cord under the machine.
   On the desk: stick the rubber feet under the table stand's base, hang the housing on it the same way, and
   run the cord back through the arch at the bottom of the stand.
3. Install BitCue: [Install](https://github.com/journeyonlabs/bitcue#install). Pick the Waveshare screen and
   answer yes to the housing question; that turns the picture and touches upside down to match the housing.

More about the screens and the installer:
[hardware/README.md](https://github.com/journeyonlabs/bitcue/blob/main/hardware/README.md).

## Help with an order

- **The 3D files:** use the store you bought them from for downloads and receipts.
- **Fit or assembly questions,** and anything about the software: open an issue on GitHub (see
  [SUPPORT.md](https://github.com/journeyonlabs/bitcue/blob/main/SUPPORT.md)).

<!--
  LINKS: the one place to edit. Every shop link on this page is defined here, and nowhere else in the repo.
  Every Amazon link carries the Amazon Associates tracking ID. Check the product before changing a link, and
  update the estimated total above if the parts change. Don't put a price next to an Amazon link: the Associates
  rules allow prices only where Amazon serves them (its own links or its API).
-->

[printables]: https://www.printables.com/model/1909246-bitcue-screen-housing-for-makera-z1-raspberry-pi-t
[cults3d]: https://cults3d.com/en/3d-model/gadget/bitcue-screen-housing-for-makera-z1-raspberry-pi-touch-screen-snap-fit-z1-mou

[amazon-waveshare-35dpi]: https://www.amazon.com/dp/B08TBF5PHH?tag=journeyon0e9-20
[amazon-microsd]: https://www.amazon.com/dp/B08L5HMJVW?tag=journeyon0e9-20
[amazon-pi-3b-plus]: https://www.amazon.com/dp/B07BDR5PDW?tag=journeyon0e9-20
[amazon-pi-4]: https://www.amazon.com/dp/B07TD42S27?tag=journeyon0e9-20
[amazon-psu-micro-usb]: https://www.amazon.com/dp/B00MARDJZ4?tag=journeyon0e9-20
[amazon-psu-usb-c]: https://www.amazon.com/dp/B07W8XHMJZ?tag=journeyon0e9-20
[amazon-adapter-micro-usb]: https://www.amazon.com/dp/B0FH5D6YP9?tag=journeyon0e9-20
[amazon-adapter-usb-c]: https://www.amazon.com/dp/B0B2NJ3P3L?tag=journeyon0e9-20
[amazon-vhb-tape]: https://www.amazon.com/dp/B007Y7EWRO?tag=journeyon0e9-20
[amazon-foam-tape]: https://www.amazon.com/dp/B0BGY2Z63K?tag=journeyon0e9-20
[amazon-rubber-feet]: https://www.amazon.com/dp/B01ACPT2LU?tag=journeyon0e9-20

[waveshare-35dpi]: https://www.waveshare.com/3.5inch-dpi-lcd.htm
[rpi-3b-plus]: https://www.raspberrypi.com/products/raspberry-pi-3-model-b-plus/
[rpi-3a-plus]: https://www.raspberrypi.com/products/raspberry-pi-3-model-a-plus/
[rpi-4]: https://www.raspberrypi.com/products/raspberry-pi-4-model-b/
[adafruit-3083]: https://www.adafruit.com/product/3083
[tape-10-color]: https://www.tapejungle.com/products/colored-electrical-tape-3-4-in-10-color-subpack-62018-d.html
