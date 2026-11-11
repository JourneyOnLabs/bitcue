# Changelog

## Unreleased

- The README and the website link the videos on YouTube (the 50-second film and the 37-second live look,
  filmed on a real job) instead of keeping video files in the repository.
- The housing's 3D files are the universal version only, for the Waveshare screen on a Pi 3A+, 3B+, 3B or 4.
  The PiTFT housing isn't sold; the software still supports the PiTFT.
- The parts list is grouped: one of each, the part for the set-up you choose (the Z1 or the desk), and
  optional extras. The VHB tape goes on as three strips of 1/2 in tape, or two of 3/4 in.
- BitCue has a website, journeyonlabs.github.io/bitcue, made from the docs folder: a front page, the parts
  list, and the demo, which plays in the browser from the README's link.
- The universal housing also comes with a table stand, for the desk next to the Z1: one piece on stick-on
  rubber feet, no tape, with the same four clicks (the screen leans back 15 to 45 degrees). The README, the
  parts list and the hardware notes describe both ways to set it up. The stand is printed and tested.
- Every part in the parts list has a shop link: Amazon, or the approved resellers for the Pi 3A+. Instead of a
  price for each part, the list gives an estimated total from November 2026 prices. The 3D files cost $8. The
  optional angled cable is now a 90° adapter at the Pi, since the power supplies' cords are fixed.
- The parts lists, the README's included, ask for industrial double-sided tape (3M VHB 4910 or 5952) for the
  housing's plate, and say how to stick it on. The thin tape that comes with the Z1 let go within an hour in
  testing.
- New plate keeps the plate before it. If the old plate turns out to be still on the Z1, Last plate (touch
  screen) or Back to the last plate (settings page) brings it back with its cut areas, and keeps the newer record
  the same way, so it can be undone. On the touch screen, New plate opens a screen with the new plate's width,
  depth and thickness, filled in from the plate on record and typed on a number pad.
- The small screen starts with a splash: the name over a strip in the bits' sticker colors, and the maker's
  logo (Journey On Labs). It shows until the screen service answers; just after a power-on, for at least 3 seconds.
  With a small screen, the installer now keeps the boot messages, the raspberry logo and the cursor off the screen
  (kernel options added to cmdline.txt, taken out again for an HDMI monitor or by the uninstaller), and the
  screen's service starts early in the boot, so the splash is the first thing after power on. Boot messages
  written after that can't draw over it: the screen program checks the Pi's text console twice a second and
  keeps it off the small screen, also before the kernel has given the console a framebuffer. Raspberry Pi OS 13
  lets only root open the console, so systemd opens it for the screen program.
- The splash's name and bars are the BitCue logo: it heads the settings page and the README, and the promo video
  ends with it and the Journey On Labs logo.
- The screens, the settings page, the installer and the services call the product BitCue; z1screen.local stays.
- A touch calibration is used only on the screen it was made on. The 3.5inch DPI screen has a built-in one, so its
  touches work without the four dots, also after the card moves from a PiTFT Pi; Set up the touch screen on the
  settings page still makes one of its own, which is saved and used instead. A PiTFT with none saved for it brings
  up the dots at the first touch, and the settings page asks for the touch screen to be set up.
- Before a start from the touch screen, a warning says what the job file was made for (material, plate size,
  thickness): an amber box on top of Check the plate, and the same line on the review screen; red, with the
  plate on record, when they differ.
- Pictures of the parts: the Pi draws each job file's part from its cutting moves (seen from above, in bronze,
  each bit at its width, without the plate around it). They show on the touch screen's job list and review
  screen, and on the phone page for the running and last job. Drawn by a separate low-priority program after a
  file is read; files read before are read again while the Z1 is idle, stopping at once for anything asked on
  the screens. tools/build_library.py adds them to a kit's library.
- New installer option `--dpi35` for the Waveshare 3.5inch DPI LCD (640 x 480, capacitive touch).
  It copies Waveshare's two overlay files to the boot drive, after checking them against the versions it was
  tested with, and switches the screen on in config.txt.
- New installer option `--flip` for a small screen mounted upside down (power cord out of the bottom): it turns
  on the settings page's "Turn the picture upside down" switch, which turns the picture and the touches 180°
  on both the PiTFT and the 3.5inch DPI screen, from the splash at power-on. `--no-flip` turns it back.
- Install on the Pi (the Mac helper) asks which screen is on the Pi and whether it hangs upside down in the
  housing, instead of assuming the PiTFT, and passes `--dpi35`, `--tft` or `--hdmi` and `--flip` or `--no-flip`
  to the installer. A user@host on its command line still skips every question.
- The README's install steps are written for a first-time setup: Raspberry Pi Imager's settings, including
  choosing the Pi's password, then the Mac helper's questions or the commands to type, and troubleshooting for
  a Pi that isn't found or a refused password. The README opens with a 43-second video (docs/promo.mp4).
- tools/make_release.sh packs only the files git tracks, as they are in the folder. It used to pack the whole
  folder, so untracked files such as local notes could end up in a release. The archives no longer carry Mac file
  attributes.
- The installer gets and checks everything it needs before it changes anything on the Pi, and refuses to edit
  a config.txt whose z1screen block has lost its end line.
- Switching screens (PiTFT, 3.5inch DPI, HDMI) takes out the previous screen's lines from config.txt.
- Phone notifications through ntfy when the Z1 stops for a bit change, and optionally a few minutes before,
  and when a job stops with an alarm or is paused (one each, with a switch for each kind). Set up under Phone
  notifications on the settings page.
- Get the hardware (docs/buy.md): the housing's 3D files (sold separately, not in this repository) and a parts list for each version (universal: one housing for the Pi 3A+, 3B+, 3B or 4 with the Waveshare 3.5inch DPI LCD; PiTFT version), linked from the README.
  hardware/README.md now has the screen and housing notes.
- A hardware question issue template and SUPPORT.md.

## 0.9.1

- Fixed: if the Pi restarted during a job, it could add an unnamed area to the plate.

## 0.9.0 (first public beta)

- Setup section on the settings page: Anchor 1, starting from the touch screen, run settings, layout folders and a PIN.
- New installs start neutral. Anchor 1 is read from the machine, the plate size is asked for, and starting from the touch screen is off.
- Changes from other websites are refused, and the Pi only answers to local names.
- A built-in demo job.
- Tests use made-up sample jobs only.
- The installer runs on the Pi, from any computer.
- GPL-3.0 license, notices and GitHub test workflow.
