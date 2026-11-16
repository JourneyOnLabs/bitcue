#!/bin/bash
# The small screen's boot settings and its upside-down switch, shared by install.sh and uninstall.sh
# (sourced, not run).
# Every block the installer adds to config.txt sits between "# z1screen: <screen>" and "# z1screen end",
# so a later install or the uninstaller can find it and take it out again. Only that start of the first
# line is looked for, so the blocks older versions added are found too. BOOT_CHANGED=yes after a
# change that needs a restart. Written for bash 3.2 and newer, with no sed -i (the tests also run on
# a Mac). Errors go to stderr; a function that fails returns 1 and changes nothing.
#
#   z1_screen_info SCREEN FIELD                   one value from the table of screens below
#   z1_screen_lines CFG SCREEN                    switch on that screen, taking out another screen's block
#   z1_screen_lines CFG none                      take out every block (for an HDMI monitor, or removing)
#   z1_stage_overlays SCREEN KIT OVERLAYS STAGE   get and check the screen's overlay files (STAGED=yes)
#   z1_install_overlays SCREEN STAGE OVERLAYS     copy the checked files into the overlays folder
#   z1_fb_found SCREEN SYSFS                      is a framebuffer of the screen's size there now
#   z1_touch_autoload DIR SCREEN|none             have the screen's touch driver loaded at every boot (a
#                                                 file in DIR, /etc/modules-load.d on the Pi), or take it out
#   z1_flip_picture STATE yes|no                  turn the picture and touches 180 degrees for a housing
#                                                 mounted upside down, or back (the settings page's switch,
#                                                 kept in STATE/screen.json; /var/lib/z1screen on the Pi)
#   z1_quiet_boot CMDLINE NOTES on|off            keep the boot text off the screen, so BitCue's splash
#                                                 shows from early in the boot (kernel options in
#                                                 cmdline.txt, listed in NOTES/cmdline-added.txt), or
#                                                 take out the options it added

BOOT_CHANGED=no
STAGED=no
TOUCH_CONF=z1screen-touch.conf
# No boot messages, no raspberry logo and no blinking cursor on the screen while the Pi starts.
QUIET_OPTS="quiet loglevel=3 logo.nologo vt.global_cursor_default=0"
QUIET_NOTES=cmdline-added.txt

# Each screen the installer knows. Adding a screen means adding its lines here and nowhere else.
#   title    the name in config.txt's "# z1screen:" line
#   detect   an overlay that, switched on by hand, means the screen is already set up
#   kms      yes: needs the KMS display driver (dtoverlay=vc4-kms-v3d), added only when it is off
#   lines    its config.txt lines, separated by spaces
#   size     its framebuffer, width,height
#   zip      where its overlay files come from; zipname: the same file in the kit's top folder
#   dtbos    the overlay files to copy, each as name:sha256 of the version the installer was tested with
#   touch    a touch driver to load at every boot. The PiTFT's overlay sets up its STMPE610 touch chip, but
#            newer kernels (seen on 6.18) don't load the driver by themselves, so touches went nowhere after
#            a restart. Nothing is named for the 3.5inch DPI screen until it has been tried.
z1_screen_info() {
  case "$1:$2" in
    #piTFT
    pitft:title) echo 'Adafruit PiTFT Plus 2.8" screen' ;;
    pitft:detect) echo 'pitft28-resistive' ;;
    pitft:lines) echo 'dtparam=spi=on dtoverlay=pitft28-resistive,rotate=90' ;;
    pitft:size) echo '320,240' ;;
    pitft:touch) echo 'stmpe_ts' ;;
    # DSI43
    dsi43:title) echo 'Waveshare 4.3inch DSI LCD' ;;
    dsi43:detect) echo 'vc4-kms-dsi-7inch' ;;
    dsi43:kms) echo 'yes' ;;
    dsi43:lines) echo 'dtoverlay=vc4-kms-v3d dtoverlay=vc4-kms-dsi-7inch' ;;
    dsi43:size) echo '800,480' ;;
    dsi43:touch) echo '' ;;
    # DPI35
    dpi35:title) echo 'Waveshare 3.5inch DPI LCD' ;;
    dpi35:detect) echo 'waveshare-35dpi' ;;
    dpi35:kms) echo 'yes' ;;
    dpi35:lines) echo 'dtoverlay=waveshare-35dpi dtoverlay=waveshare-touch-35dpi' ;;
    dpi35:size) echo '640,480' ;;
    dpi35:zip) echo 'https://files.waveshare.com/wiki/3.5inch%20DPI%20LCD/3.5DPI-dtbo.zip' ;;
    dpi35:zipname) echo '3.5DPI-dtbo.zip' ;;
    # From Waveshare's 3.5DPI-dtbo.zip as downloaded on 25 October 2026.
    dpi35:dtbos) echo 'waveshare-35dpi.dtbo:a54e9bbe51c28c3703f93447c3d56da489c595fc7e5bef8423055b1e4cdbf949' \
                      'waveshare-touch-35dpi.dtbo:0e0aaea9a7515cc27a3d7b49cc26056ec1859912209182b065fb04f258f3992a' ;;
  esac
}

# True if CFG switches on overlay NAME for every Pi: a "dtoverlay=NAME" line (the whole name, with or
# without parameters) that isn't commented out and sits before any [section] or under [all]. A line
# under [pi4] or another filter doesn't count, because it may not apply to this Pi.
z1_overlay_on() {
  awk -v name="$2" '
    BEGIN { all = 1; want = "dtoverlay=" name }
    { line = $0; sub(/\r$/, "", line); sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+$/, "", line) }
    line ~ /^\[/ { all = (line == "[all]"); next }
    all && index(line, want) == 1 {
      rest = substr(line, length(want) + 1)
      if (rest == "" || rest ~ /^[,[:space:]#]/) found = 1
    }
    END { exit !found }
  ' "$1"
}

# True if every "# z1screen:" line in CFG has its "# z1screen end" line, so the blocks can be taken out.
z1_blocks_closed() {
  awk '{ sub(/\r$/, "") } /^# z1screen: / { open = 1 } $0 == "# z1screen end" { open = 0 } END { exit open }' "$1"
}

# True if CFG has the "# z1screen:" line of the screen titled $2.
z1_has_block() {
  awk -v mark="# z1screen: $2" '{ sub(/\r$/, "") } index($0, mark) == 1 { found = 1 } END { exit !found }' "$1"
}

# Takes out the z1screen blocks: all of them, or (with a title) all but that screen's. A blank line just
# before a block was added with it, so it goes too. Other lines keep their own line endings. Refuses,
# changing nothing, if a block has no end line (anything after it would be lost).
z1_remove_blocks() {
  local cfg="$1" keep="${2:-}"
  grep -q '^# z1screen: ' "$cfg" || return 0
  if ! z1_blocks_closed "$cfg"; then
    echo "$cfg has a '# z1screen:' line with no '# z1screen end' line after it. Nothing was changed." >&2
    echo "Fix it by hand (the copy from before the first install is $cfg.before-z1screen), then run this again." >&2
    return 1
  fi
  awk -v keep="# z1screen: $keep" '
    { line = $0; sub(/\r$/, "", line) }
    !inblock && line ~ /^# z1screen: / && (keep == "# z1screen: " || index(line, keep) != 1) {
      inblock = 1; blank = 0; next
    }
    inblock { if (line == "# z1screen end") inblock = 0; next }
    blank { print held; blank = 0 }
    line == "" { blank = 1; held = $0; next }
    { print }
    END { if (blank) print held }
  ' "$cfg" > "$cfg.z1tmp"
  if cmp -s "$cfg" "$cfg.z1tmp"; then
    rm -f "$cfg.z1tmp"
    return 0
  fi
  cat "$cfg.z1tmp" > "$cfg"
  rm -f "$cfg.z1tmp"
  BOOT_CHANGED=yes
  echo "    took out the earlier screen's lines"
}

z1_screen_lines() {
  local cfg="$1" screen="$2" title lines="" l
  if [ "$screen" = none ]; then
    z1_remove_blocks "$cfg"
    return
  fi
  title="$(z1_screen_info "$screen" title)"
  if [ -z "$title" ]; then
    echo "Unknown screen: $screen" >&2
    return 1
  fi
  z1_remove_blocks "$cfg" "$title" || return 1
  if z1_has_block "$cfg" "$title" || z1_overlay_on "$cfg" "$(z1_screen_info "$screen" detect)"; then
    echo "    already switched on"
    return 0
  fi
  # Pi OS turns on the KMS display driver near the top of config.txt; add it only when it is off.
  if [ "$(z1_screen_info "$screen" kms)" = yes ] && ! z1_overlay_on "$cfg" vc4-kms-v3d; then
    lines='dtoverlay=vc4-kms-v3d\n'
  fi
  for l in $(z1_screen_info "$screen" lines); do
    lines="$lines$l\n"
  done
  [ -f "$cfg.before-z1screen" ] || cp "$cfg" "$cfg.before-z1screen"
  printf "\n# z1screen: %s (added by the BitCue installer)\n[all]\n${lines}# z1screen end\n" "$title" >> "$cfg"
  BOOT_CHANGED=yes
}

# Names the screen's touch driver (its "touch" in the table) in DIR/z1screen-touch.conf, so it is loaded at
# every boot; a screen with none, or "none", takes that file out.
z1_touch_autoload() {
  local dir="$1" screen="$2" module conf
  conf="$dir/$TOUCH_CONF"
  module="$(z1_screen_info "$screen" touch)"
  if [ -z "$module" ]; then
    if [ -f "$conf" ]; then
      rm -f "$conf"
      echo "    took out $conf"
    fi
    return 0
  fi
  local want
  want="$(printf '# The touch driver of the %s, loaded at every boot (added by the BitCue installer)\n%s\n' \
    "$(z1_screen_info "$screen" title)" "$module")"
  if [ -f "$conf" ] && [ "$(cat "$conf")" = "$want" ]; then
    echo "    already loaded at boot"
    return 0
  fi
  mkdir -p "$dir"
  printf '%s\n' "$want" > "$conf"
  echo "    $module loaded at every boot ($conf)"
}

# True if the kernel command line LINE has OPT, or the same KEY= with a value of its own (loglevel=7 set by
# hand is left alone).
z1_has_opt() {
  case " $1 " in *" $2 "*) return 0 ;; esac
  case "$2" in *=*) case " $1 " in *" ${2%%=*}="*) return 0 ;; esac ;; esac
  return 1
}

# on: adds the QUIET_OPTS that CMDLINE (cmdline.txt, one line) doesn't have yet, and lists them in
# NOTES/cmdline-added.txt, since cmdline.txt can't hold a comment. off: takes out only the options listed
# there, so options that were there before and everything else on the line stay. The first change keeps a
# copy of the file as CMDLINE.before-z1screen. BOOT_CHANGED=yes after a change.
z1_quiet_boot() {
  local cmdline="$1" notes="$2/$QUIET_NOTES" want="$3" line new="" tok opt added="" skip removed=no
  if [ ! -f "$cmdline" ]; then
    echo "z1_quiet_boot: there is no $cmdline" >&2
    return 1
  fi
  line="$(head -n 1 "$cmdline" | tr -d '\r')"
  set -f
  if [ "$want" = on ]; then
    new="$line"
    for opt in $QUIET_OPTS; do
      if ! z1_has_opt "$new" "$opt"; then
        new="$new${new:+ }$opt"
        added="$added${added:+ }$opt"
      fi
    done
  else
    [ -f "$notes" ] && added="$(cat "$notes")"
    for tok in $line; do
      skip=no
      for opt in $added; do
        [ "$tok" = "$opt" ] && skip=yes
      done
      if [ "$skip" = yes ]; then removed=yes; else new="$new${new:+ }$tok"; fi
    done
    [ "$removed" = yes ] || new="$line"     # nothing of ours there: leave the line exactly as it is
  fi
  set +f
  if [ "$new" = "$line" ]; then
    [ "$want" = on ] && echo "    the boot is already quiet"
    [ "$want" = on ] || rm -f "$notes"
    return 0
  fi
  [ -f "$cmdline.before-z1screen" ] || cp -p "$cmdline" "$cmdline.before-z1screen"
  printf '%s\n' "$new" > "$cmdline.tmp" && mv "$cmdline.tmp" "$cmdline"
  if [ "$want" = on ]; then
    mkdir -p "$2"
    if [ -f "$notes" ]; then
      printf '%s %s\n' "$(cat "$notes")" "$added" > "$notes"
    else
      printf '%s\n' "$added" > "$notes"
    fi
    echo "    added to $cmdline: $added"
  else
    rm -f "$notes"
    echo "    took out of $cmdline: $added"
  fi
  BOOT_CHANGED=yes
}

# Sets "flip" (yes: true, no: false) in STATE/screen.json, where the settings page keeps its "Turn the
# picture upside down" switch, keeping the file's other choices. That file wins over every settings file
# (a z1screen.json made from config.example.json says "flip": false), and the switch shows what is set.
# tft.py turns the picture before it writes it to the framebuffer and touch.py turns the touches, the same
# way for every screen, so nothing goes in config.txt or cmdline.txt and no restart is needed. A touch
# calibration made either way up keeps working (touch.apply).
z1_flip_picture() {
  local file="$1/screen.json" want="${2:-yes}" got
  case "$want" in yes|no) ;; *) echo "z1_flip_picture: yes or no, not $want" >&2; return 1 ;; esac
  mkdir -p "$1"
  if ! got="$(python3 -c "import json, os, sys
path, want = sys.argv[1], sys.argv[2] == 'yes'
try:
    with open(path, encoding='utf-8') as fh:
        prefs = json.load(fh)
except (OSError, ValueError):
    prefs = {}
if not isinstance(prefs, dict):
    prefs = {}
if prefs.get('flip') is want:
    print('same')
    sys.exit(0)
prefs['flip'] = want
with open(path + '.tmp', 'w', encoding='utf-8') as fh:
    json.dump(prefs, fh)
os.replace(path + '.tmp', path)
print('changed')" "$file" "$want")"; then
    echo "Could not write $file." >&2
    return 1
  fi
  if [ "$want" = yes ] && [ "$got" = same ]; then
    echo "    already upside down"
  elif [ "$want" = yes ]; then
    echo "    the picture and touches are turned upside down ($file)"
  elif [ "$got" = same ]; then
    echo "    already the right way up"
  else
    echo "    the picture and touches are the right way up again ($file)"
  fi
}

z1_sha256() {
  python3 -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" "$1"
}

# True if every file named in PINS (name:sha256 ...) is in DIR with those contents.
z1_overlays_ok() {
  local dir="$1" pin
  for pin in $2; do
    [ -f "$dir/${pin%%:*}" ] && [ "$(z1_sha256 "$dir/${pin%%:*}")" = "${pin#*:}" ] || return 1
  done
}

# Python's own download, so the installer needs no other package for it.
z1_download() {
  python3 -c "import sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=60) as r, open(sys.argv[2], 'wb') as fh:
    fh.write(r.read())" "$1" "$2" 2>/dev/null
}

# Gets the screen's overlay files into STAGE and checks them against the table, before anything on the
# Pi is changed: from the zip in the kit's top folder if it is there, else downloaded. Nothing to do
# (STAGED=no) for a screen without overlay files, or when OVERLAYS already holds the right ones and
# the kit has no zip.
z1_stage_overlays() {
  local screen="$1" kit="$2" overlays="$3" stage="$4" pins url name zip pin src got
  STAGED=no
  pins="$(z1_screen_info "$screen" dtbos)"
  [ -n "$pins" ] || return 0
  url="$(z1_screen_info "$screen" zip)"
  name="$(z1_screen_info "$screen" zipname)"
  zip="$kit/$name"
  if [ ! -f "$zip" ]; then
    if z1_overlays_ok "$overlays" "$pins"; then
      echo "    already there"
      return 0
    fi
    zip="$stage/download.zip"
    if ! z1_download "$url" "$zip"; then
      echo "Could not download the overlay files from $url." >&2
      echo "Download $name on a computer, put it in the kit's top folder, and run this again." >&2
      return 1
    fi
  fi
  rm -rf "$stage/unzipped"
  if ! python3 -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$zip" "$stage/unzipped" 2>/dev/null; then
    echo "Can't open $zip as a zip file." >&2
    return 1
  fi
  for pin in $pins; do
    src="$(find "$stage/unzipped" -type f -name "${pin%%:*}" | head -n 1)"
    if [ -z "$src" ]; then
      echo "${pin%%:*} is not in $zip." >&2
      return 1
    fi
    got="$(z1_sha256 "$src")"
    if [ "$got" != "${pin#*:}" ]; then
      echo "${pin%%:*} in $zip is not the version this installer was tested with (SHA-256 $got)." >&2
      echo "Nothing was changed. Get a newer BitCue release, made for Waveshare's current files." >&2
      return 1
    fi
    cp "$src" "$stage/${pin%%:*}"
  done
  STAGED=yes
}

# Copies the files z1_stage_overlays checked into OVERLAYS. Files already there with the same contents are left alone.
z1_install_overlays() {
  local screen="$1" stage="$2" overlays="$3" pin f
  for pin in $(z1_screen_info "$screen" dtbos); do
    f="${pin%%:*}"
    if ! cmp -s "$stage/$f" "$overlays/$f"; then
      cp "$stage/$f" "$overlays/$f"
      BOOT_CHANGED=yes
      echo "    copied $f to $overlays"
    fi
  done
}

# True if SYSFS (normally /sys/class/graphics) has a framebuffer of the screen's size, either way round.
z1_fb_found() {
  local size f
  size="$(z1_screen_info "$1" size)"
  for f in "$2"/fb*/virtual_size; do
    [ -e "$f" ] || continue
    case "$(cat "$f")" in "$size"|"${size#*,},${size%,*}") return 0 ;; esac
  done
  return 1
}
