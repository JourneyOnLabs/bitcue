#!/bin/bash
# Removes BitCue from the Pi. From the kit folder:  sudo bash install/uninstall.sh
set -u
systemctl disable --now z1screen-tft.service z1screen-kiosk.service z1screen.service 2>/dev/null
rm -f /etc/systemd/system/z1screen.service /etc/systemd/system/z1screen-tft.service \
  /etc/systemd/system/z1screen-kiosk.service
rm -f /etc/pam.d/z1screen-kiosk /etc/NetworkManager/conf.d/z1screen-wifi.conf /etc/z1screen/config.json
BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot
if [ -f "$BOOT/config.txt" ]; then
  # shellcheck source=boot-screen.sh
  . "$(dirname "$0")/boot-screen.sh"
  z1_screen_lines "$BOOT/config.txt" none >/dev/null
  z1_touch_autoload /etc/modules-load.d none >/dev/null
  if [ "$BOOT_CHANGED" = yes ]; then
    echo "Took the small screen's lines out of $BOOT/config.txt (the screen goes blank after the next restart)."
  fi
  if [ -f "$BOOT/cmdline.txt" ] && z1_quiet_boot "$BOOT/cmdline.txt" /etc/z1screen off | grep -q .; then
    echo "Took the quiet-boot options out of $BOOT/cmdline.txt (the boot text shows again)."
  fi
fi
rmdir /etc/z1screen 2>/dev/null
systemctl enable getty@tty1.service 2>/dev/null
systemctl daemon-reload
rm -rf /opt/z1screen
echo "Removed. Job profiles, the plate record, bit colors and the settings page's choices are kept in /var/lib/z1screen"
echo "(delete that folder to remove them too). The settings file z1screen.json stays on the SD card's boot drive."
