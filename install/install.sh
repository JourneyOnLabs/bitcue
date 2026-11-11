#!/bin/bash
# BitCue: installer for the Raspberry Pi. Run it on the Pi, from the unpacked kit:
#   sudo bash install/install.sh [--dpi35 | --hdmi] [--flip | --no-flip] [--no-reboot] [--config FILE]
#
#   (no option)    the screen is the Adafruit PiTFT Plus 2.8" sitting on the Pi (320 x 240)
#   --dpi35        the screen is the Waveshare 3.5inch DPI LCD sitting on the Pi (640 x 480, capacitive touch).
#                  Its two overlay files come from Waveshare's 3.5DPI-dtbo.zip: put the zip in the kit's
#                  top folder, or the installer downloads it. Either way they are checked against the
#                  versions the installer was tested with (install/boot-screen.sh) before anything changes.
#   --hdmi         the screen is a monitor on the Pi's HDMI port instead
#   --flip         the small screen is mounted upside down (a housing with the power cord coming out of the
#                  bottom): its picture and touches are turned 180 degrees. The same as the settings page's
#                  switch "Turn the picture upside down", which can turn it back.
#   --no-flip      the small screen is the right way up again. With neither, an earlier choice is kept.
#   --no-reboot    don't restart the Pi at the end, even when the small screen needs it
#   --config FILE  use FILE as the settings file (z1screen.json on the SD card's boot drive),
#                  replacing the one there. A z1screen.json in the kit's top folder is used the same way.
#
# Made for Raspberry Pi OS Lite (64-bit), Bookworm or newer.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=tft
SCREEN=pitft
REBOOT=yes
FLIP=
SETTINGS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --hdmi) MODE=hdmi ;;
    --tft) MODE=tft; SCREEN=pitft ;;
    --dpi35) MODE=tft; SCREEN=dpi35 ;;
    --flip) FLIP=yes ;;
    --no-flip) FLIP=no ;;
    --no-reboot) REBOOT=no ;;
    --config) shift; SETTINGS="${1:-}" ;;
    --config=*) SETTINGS="${1#--config=}" ;;
    *) echo "Unknown option: $1  (the options are --dpi35, --hdmi, --flip, --no-flip, --no-reboot and --config FILE)"; exit 1 ;;
  esac
  shift
done
if [ -n "$FLIP" ] && [ "$MODE" = hdmi ]; then
  echo "--flip and --no-flip turn the small screen on the Pi. To turn a monitor's picture, use the monitor's own menu."
  exit 1
fi
if [ -z "$SETTINGS" ] && [ -f "$KIT/z1screen.json" ]; then
  SETTINGS="$KIT/z1screen.json"
fi
if [ -n "$SETTINGS" ] && [ ! -f "$SETTINGS" ]; then
  echo "Can't find the settings file $SETTINGS"
  exit 1
fi
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$KIT/z1screen/__init__.py")"
echo "BitCue $VERSION"
if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash $0 $*"
  exit 1
fi

BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot
CFG="$BOOT/config.txt"
if [ ! -f "$CFG" ]; then
  echo "Can't find $CFG. This installer is made for Raspberry Pi OS on a Raspberry Pi."
  exit 1
fi
NEED_REBOOT=no
# shellcheck source=boot-screen.sh
. "$KIT/install/boot-screen.sh"
export DEBIAN_FRONTEND=noninteractive

if [ "$MODE" = hdmi ]; then
  KUSER="${SUDO_USER:-}"
  if [ -z "$KUSER" ] || [ "$KUSER" = "root" ]; then
    KUSER="$(getent passwd 1000 | cut -d: -f1 || true)"
  fi
  if [ -z "$KUSER" ]; then
    echo "Could not find the Pi's user account."
    exit 1
  fi
  echo "==> Installing the browser and display packages (this takes a few minutes)"
  apt-get update -y
  CHPKG=chromium
  if ! apt-cache show chromium >/dev/null 2>&1; then CHPKG=chromium-browser; fi
  apt-get install -y cage "$CHPKG" python3 curl fonts-dejavu-core
  CHROMIUM="$(command -v chromium || command -v chromium-browser || true)"
  if [ -z "$CHROMIUM" ]; then
    echo "Chromium did not install. Check the Pi's internet connection and run this again."
    exit 1
  fi
else
  echo "==> Installing the drawing package for the small screen (a minute or two)"
  apt-get update -y
  apt-get install -y python3 python3-pil
  if ! python3 -c "from PIL import Image, ImageChops, ImageDraw, ImageFont" 2>/dev/null; then
    echo "Pillow (python3-pil) did not install. Check the Pi's internet connection and run this again."
    exit 1
  fi
fi

# Checks and files from the internet come first, so a problem here changes nothing on the Pi.
if ! z1_blocks_closed "$CFG"; then
  echo "$CFG has a '# z1screen:' line with no '# z1screen end' line after it. Nothing was changed."
  echo "Fix it by hand (the copy from before the first install is $CFG.before-z1screen), then run this again."
  exit 1
fi
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
if [ "$MODE" = tft ] && [ -n "$(z1_screen_info "$SCREEN" dtbos)" ]; then
  echo "==> Overlay files for the $(z1_screen_info "$SCREEN" title)"
  z1_stage_overlays "$SCREEN" "$KIT" "$BOOT/overlays" "$STAGE" || exit 1
fi

echo "==> Copying the screen software to /opt/z1screen"
install -d /opt/z1screen
rm -rf /opt/z1screen/z1screen /opt/z1screen/library
cp -r "$KIT/z1screen" "$KIT/library" /opt/z1screen/
find /opt/z1screen -name '__pycache__' -prune -exec rm -rf {} +
id z1screen >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin z1screen

echo "==> Settings file on the SD card's boot drive"
if [ -n "$SETTINGS" ]; then
  if ! python3 -c "import json, sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$SETTINGS"; then
    echo "$SETTINGS is not a valid settings file (JSON). Nothing was changed."
    exit 1
  fi
  if [ -f "$BOOT/z1screen.json" ] && ! cmp -s "$SETTINGS" "$BOOT/z1screen.json"; then
    cp "$BOOT/z1screen.json" "$BOOT/z1screen.json.before-$(date +%Y%m%d-%H%M%S)"
  fi
  cp "$SETTINGS" "$BOOT/z1screen.json"
  echo "    using $(basename "$SETTINGS")"
elif [ ! -f "$BOOT/z1screen.json" ]; then
  cp "$KIT/config.example.json" "$BOOT/z1screen.json"
fi
install -d /etc/z1screen
printf '{"display": "%s"}\n' "$MODE" > /etc/z1screen/config.json
if [ -n "$FLIP" ]; then
  echo "==> Which way up the small screen is mounted"
  z1_flip_picture /var/lib/z1screen "$FLIP" || exit 1
  chown z1screen:z1screen /var/lib/z1screen /var/lib/z1screen/screen.json
fi

if [ "$MODE" = tft ]; then
  echo "==> Checking that the small screen can be drawn"
  printf '{"mode": "_starting"}\n' > /tmp/z1screen-view.json
  if ! PYTHONPATH=/opt/z1screen python3 -m z1screen.tft --png /tmp/z1screen-test.png --view /tmp/z1screen-view.json >/dev/null; then
    echo "The screen program could not draw a test picture (see the message above)."
    exit 1
  fi
  rm -f /tmp/z1screen-view.json /tmp/z1screen-test.png

  if [ "$STAGED" = yes ]; then
    echo "==> Copying the overlay files to $BOOT/overlays"
    z1_install_overlays "$SCREEN" "$STAGE" "$BOOT/overlays"
  fi

  echo "==> Switching on the $(z1_screen_info "$SCREEN" title) in $CFG"
  z1_screen_lines "$CFG" "$SCREEN"
  z1_fb_found "$SCREEN" /sys/class/graphics || NEED_REBOOT=yes
  echo "==> The touch driver"
  z1_touch_autoload /etc/modules-load.d "$SCREEN"
  TOUCH="$(z1_screen_info "$SCREEN" touch)"
  if [ -n "$TOUCH" ] && ! grep -q "^$TOUCH " /proc/modules 2>/dev/null; then
    modprobe "$TOUCH" 2>/dev/null || true     # touch works now, without waiting for the next restart
  fi
  echo "==> A quiet boot: BitCue's splash instead of the boot text"
  if [ -f "$BOOT/cmdline.txt" ]; then
    z1_quiet_boot "$BOOT/cmdline.txt" /etc/z1screen on
  fi
else
  z1_screen_lines "$CFG" none
  z1_touch_autoload /etc/modules-load.d none
  if [ -f "$BOOT/cmdline.txt" ]; then
    z1_quiet_boot "$BOOT/cmdline.txt" /etc/z1screen off
  fi
fi
[ "$BOOT_CHANGED" = yes ] && NEED_REBOOT=yes

echo "==> Services that start at power on"
install -m 644 "$KIT/install/z1screen.service" /etc/systemd/system/z1screen.service
if [ "$MODE" = tft ]; then
  install -m 644 "$KIT/install/z1screen-tft.service" /etc/systemd/system/z1screen-tft.service
  if [ -f /etc/systemd/system/z1screen-kiosk.service ]; then
    systemctl disable --now z1screen-kiosk.service >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/z1screen-kiosk.service /etc/pam.d/z1screen-kiosk
  fi
  systemctl set-default multi-user.target >/dev/null
else
  sed -e "s|@USER@|$KUSER|g" -e "s|@CHROMIUM@|$CHROMIUM|g" \
    "$KIT/install/z1screen-kiosk.service" > /etc/systemd/system/z1screen-kiosk.service
  chmod 644 /etc/systemd/system/z1screen-kiosk.service
  install -m 644 "$KIT/install/pam-z1screen-kiosk" /etc/pam.d/z1screen-kiosk
  for g in video render input; do
    getent group "$g" >/dev/null && usermod -aG "$g" "$KUSER" || true
  done
  if [ -f /etc/systemd/system/z1screen-tft.service ]; then
    systemctl disable --now z1screen-tft.service >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/z1screen-tft.service
  fi
  systemctl set-default graphical.target >/dev/null
fi

# Keep Wi-Fi from dozing, so the screen and Makera Studio stay connected
if [ -d /etc/NetworkManager/conf.d ]; then
  printf '[connection]\nwifi.powersave = 2\n' > /etc/NetworkManager/conf.d/z1screen-wifi.conf
fi

# The screen replaces the text login on tty1 (other consoles still work: Ctrl+Alt+F2)
systemctl disable getty@tty1.service >/dev/null 2>&1 || true
systemctl daemon-reload
if [ "$MODE" = tft ]; then
  systemctl enable z1screen.service
  systemctl reenable z1screen-tft.service     # it starts early in the boot now (sysinit.target, not multi-user)
  systemctl restart z1screen.service
  systemctl restart z1screen-tft.service
else
  systemctl enable z1screen.service z1screen-kiosk.service
  systemctl restart z1screen.service
  systemctl restart z1screen-kiosk.service || true
fi

HOST="$(hostname)"
echo
echo "Done (BitCue $VERSION). The screen starts by itself every time the Pi is powered on."
echo "Settings page (phone or laptop on the same Wi-Fi):  http://$HOST.local:8080/settings"
echo "Open it once to finish the setup (plate size, Anchor 1, bit colors)."
echo "In Makera Studio, connect to the machine whose name ends in _screen."
if [ "$NEED_REBOOT" = yes ]; then
  if [ "$REBOOT" = yes ]; then
    echo "The Pi restarts in 5 seconds to switch on the small screen. The job screen appears on it about a minute later."
    systemd-run --on-active=5 --unit=z1screen-restart /bin/systemctl reboot >/dev/null 2>&1 || {
      sleep 5
      systemctl reboot
    }
  else
    echo "Restart the Pi to switch on the small screen:  sudo reboot"
  fi
fi
