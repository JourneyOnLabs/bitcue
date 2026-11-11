#!/bin/bash
# Run on a Mac or Linux computer (on a Mac, double-click "Install on the Pi.command" next to this file).
# Copies BitCue to the Raspberry Pi over the Wi-Fi and installs it.
# You type the Pi's password once. (On Windows, or without this script: see README.md, "Install".)
#
#   bash install-to-pi.sh                    asks which screen (what double-clicking does)
#   bash install-to-pi.sh --tft              for the 2.8-inch PiTFT screen on the Pi
#   bash install-to-pi.sh --dpi35            for the Waveshare 3.5inch DPI screen on the Pi
#   bash install-to-pi.sh --hdmi             for a monitor on the Pi's HDMI port instead
#   bash install-to-pi.sh --dpi35 --flip     a screen mounted upside down: the picture and touches turned 180 degrees
#   bash install-to-pi.sh pi@z1screen.local  to skip the questions
# With no user@host it asks for the Pi's user name and host name, and without a screen option also which
# screen is on the Pi and whether it hangs upside down in the housing.
# A z1screen.json next to this script is installed as the Pi's settings file. A 3.5DPI-dtbo.zip next to it
# (Waveshare's overlay files for the 3.5inch DPI screen) is copied too, so the Pi doesn't have to download it.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET=""
FLAGS=""
for a in "$@"; do
  case "$a" in
    --*) FLAGS="$FLAGS $a" ;;
    *) TARGET="$a" ;;
  esac
done
if [ -z "$TARGET" ]; then
  # Asked only without a screen option: double-clicking "Install on the Pi" passes none.
  case " $FLAGS " in
    *" --dpi35 "*|*" --hdmi "*|*" --tft "*) ;;
    *)
      echo "Which screen is on the Pi?"
      echo "  1) Waveshare 3.5inch DPI LCD (the universal housing)"
      echo "  2) Adafruit PiTFT Plus 2.8\""
      echo "  3) A monitor on the Pi's HDMI port"
      read -r -p "Screen [1]: " PICK
      case "${PICK:-1}" in
        1) FLAGS="$FLAGS --dpi35" ;;
        2) FLAGS="$FLAGS --tft" ;;
        3) FLAGS="$FLAGS --hdmi" ;;
        *) echo "Please run it again and answer 1, 2 or 3."; exit 1 ;;
      esac
      ;;
  esac
  case " $FLAGS " in
    *" --hdmi "*|*" --flip "*|*" --no-flip "*) ;;
    *)
      read -r -p "Is the screen in the BitCue housing, hanging with the power cord out of the bottom? [Y/n]: " HUNG
      case "${HUNG:-y}" in
        [Nn]*) FLAGS="$FLAGS --no-flip" ;;
        *) FLAGS="$FLAGS --flip" ;;
      esac
      ;;
  esac
  ME="$(id -un)"
  read -r -p "User name you set for the Pi in Raspberry Pi Imager [$ME]: " PIUSER
  read -r -p "Pi host name [z1screen]: " PIHOST
  TARGET="${PIUSER:-$ME}@${PIHOST:-z1screen}.local"
fi
HOST="${TARGET#*@}"
# A re-flashed Pi has a new key; forget the old one so ssh does not refuse to connect.
ssh-keygen -R "$HOST" >/dev/null 2>&1 || true
SOCK="/tmp/z1screen-ssh-$$"
SSH=(ssh -o ControlMaster=auto -o ControlPath="$SOCK" -o ControlPersist=180 -o StrictHostKeyChecking=accept-new)
echo "Connecting to $TARGET (type the password you set for the Pi in Raspberry Pi Imager when asked;"
echo "nothing shows while you type) ..."
"${SSH[@]}" "$TARGET" 'rm -rf ~/z1screen-kit && mkdir -p ~/z1screen-kit'
echo "Copying files ..."
EXTRA=""
[ -f "$DIR/z1screen.json" ] && EXTRA="z1screen.json"
[ -f "$DIR/3.5DPI-dtbo.zip" ] && EXTRA="$EXTRA 3.5DPI-dtbo.zip"
# --no-xattrs: no Mac file attributes in the copy (the Pi's tar would print a warning for each file)
COPYFILE_DISABLE=1 tar -C "$DIR" --no-xattrs --exclude '__pycache__' -czf - z1screen library install config.example.json $EXTRA \
  | "${SSH[@]}" "$TARGET" 'tar -C ~/z1screen-kit -xzf -'
echo "Installing (about 5 minutes) ..."
LOG="/tmp/z1screen-install-$$.txt"
set -o pipefail
"${SSH[@]}" -t "$TARGET" "sudo bash ~/z1screen-kit/install/install.sh$FLAGS" | tee "$LOG"
"${SSH[@]}" -O exit "$TARGET" >/dev/null 2>&1 || true

# Wait for the screen to come up (the Pi may be restarting), then open the settings page.
echo "Waiting for the screen to start ..."
if grep -q "restarts in" "$LOG"; then sleep 40; else sleep 3; fi
rm -f "$LOG"
for _ in $(seq 1 60); do
  if curl -fs --max-time 3 "http://$HOST:8080/health" >/dev/null 2>&1; then
    echo "All done. Opening the settings page: http://$HOST:8080/settings"
    open "http://$HOST:8080/settings" 2>/dev/null || xdg-open "http://$HOST:8080/settings" 2>/dev/null || true
    exit 0
  fi
  sleep 3
done
echo "Installed, but the screen did not answer within 3 minutes."
echo "Check that the Pi is on, then open http://$HOST:8080/settings"
