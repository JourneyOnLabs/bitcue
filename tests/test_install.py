"""The installer's changes to the Pi's config.txt and overlays folder (install/boot-screen.sh), run with
bash on copies in a temporary folder, the upside-down switch it sets for --flip, and the order of the
steps in install/install.sh. Nothing here
needs a Pi, root or the network (downloads come from file:// addresses)."""
import copy, hashlib, json, os, re, shutil, subprocess, tempfile, zipfile
from common import ROOT, HERE, SIGN, check, make_library
from z1screen import config, touch as touch_mod
from z1screen.hub import Hub
from z1screen.jobs import JobStore

LIB = os.path.join(ROOT, "install", "boot-screen.sh")
INSTALL = os.path.join(ROOT, "install", "install.sh")
STOCK = open(os.path.join(HERE, "fixtures", "config-bookworm.txt"), encoding="utf-8").read()
# The block 0.9.1 added for the PiTFT, as it sits on Pis installed before the 3.5inch DPI screen (and before the
# product was called BitCue).
OLD_PITFT = ('\n# z1screen: Adafruit PiTFT Plus 2.8" screen (added by the Z1 job screen installer)\n[all]\n'
             'dtparam=spi=on\ndtoverlay=pitft28-resistive,rotate=90\n# z1screen end\n')
DTBOS = ("vc4-kms-DPI-35inch.dtbo", "waveshare-35dpi-3b-4b.dtbo", "waveshare-35dpi-3b.dtbo",
         "waveshare-35dpi-4b.dtbo", "waveshare-35dpi.dtbo", "waveshare-touch-35dpi.dtbo")
WORK = tempfile.mkdtemp()
CFG = os.path.join(WORK, "config.txt")
# A made-up screen with made-up overlay files, so the overlay steps can be tried with files the test makes.
TEST_SCREEN = r'''
eval "orig_$(declare -f z1_screen_info)"
z1_screen_info() {
  case "$1:$2" in
    test:title) echo 'Test screen' ;;
    test:zip) echo "$TEST_URL" ;;
    test:zipname) echo 'test-dtbo.zip' ;;
    test:dtbos) echo "$TEST_PINS" ;;
    *) orig_z1_screen_info "$@" ;;
  esac
}
'''


def run(script, *args, env=None):
    """Sources the library (with set -e, as install.sh does) and runs script; returns (exit code, output, BOOT_CHANGED)."""
    p = subprocess.run(["bash", "-c", '. "$0"; set -e; %s\n%s; echo "BOOT_CHANGED=$BOOT_CHANGED"' % (TEST_SCREEN, script),
                        LIB] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                       env=dict(os.environ, **(env or {})))
    out = p.stdout
    changed = out.rstrip().rsplit("BOOT_CHANGED=", 1)[-1] if "BOOT_CHANGED=" in out else ""
    return p.returncode, out, changed


def lines(screen, cfg=CFG):
    return run('z1_screen_lines "$1" "$2"', cfg, screen)


def on(name, cfg=CFG):
    return run('if z1_overlay_on "$1" "$2"; then echo ON; else echo OFF; fi', cfg, name)[1].startswith("ON")


def setup(text):
    with open(CFG, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    if os.path.exists(CFG + ".before-z1screen"):
        os.remove(CFG + ".before-z1screen")


def read(path=CFG):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def active(text, line):
    return sum(1 for l in text.splitlines() if l.strip() == line)


def body(name):
    return ("overlay " + name).encode()


def dtbo_zip(path, names=DTBOS, content=body):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3.5DPI-dtbo/", b"")
        for n in names:
            z.writestr("3.5DPI-dtbo/" + n, content(n))


def pins(names=("waveshare-35dpi.dtbo", "waveshare-touch-35dpi.dtbo")):
    return " ".join("%s:%s" % (n, hashlib.sha256(body(n)).hexdigest()) for n in names)


print("3.5inch DPI screen on a fresh Raspberry Pi OS")
setup(STOCK)
code, out, changed = lines("dpi35")
text = read()
check(code == 0 and changed == "yes", "the lines are added and a restart is asked for")
check(text.startswith(STOCK), "the rest of config.txt is left as it was")
check(active(text, "dtoverlay=waveshare-35dpi") == 1 and active(text, "dtoverlay=waveshare-touch-35dpi") == 1,
      "screen and touch overlays switched on")
check(active(text, "dtoverlay=vc4-kms-v3d") == 1, "the KMS driver Pi OS already turns on is not added twice")
check("# z1screen: Waveshare 3.5inch DPI LCD" in text and text.rstrip().endswith("# z1screen end"),
      "the block is marked so it can be taken out again")
check(text[len(STOCK):].splitlines()[1] == "# z1screen: Waveshare 3.5inch DPI LCD (added by the BitCue installer)",
      "and says it was added by the BitCue installer")
check(text[len(STOCK):].splitlines()[2] == "[all]", "the block applies to every Pi model")
check(read(CFG + ".before-z1screen") == STOCK, "a copy of the original is kept")

code, out, changed = lines("dpi35")
check(code == 0 and changed == "no" and read() == text and "already switched on" in out,
      "running the installer again changes nothing")

print("the KMS driver is added when it is off")
setup(STOCK.replace("\ndtoverlay=vc4-kms-v3d\n", "\n#dtoverlay=vc4-kms-v3d\n"))
lines("dpi35")
block = read().split("# z1screen: ", 1)[1]
check(active(block, "dtoverlay=vc4-kms-v3d") == 1, "dtoverlay=vc4-kms-v3d inside the block")

print("which lines count as switched on")
no_kms = STOCK.replace("\ndtoverlay=vc4-kms-v3d\n", "\n")
setup(no_kms + "\n[pi4]\ndtoverlay=vc4-kms-v3d\n")
check(not on("vc4-kms-v3d"), "a line under [pi4] doesn't count: it may not apply to this Pi")
lines("dpi35")
block = read().split("# z1screen: ", 1)[1]
check(active(block, "dtoverlay=vc4-kms-v3d") == 1, "so the DPI block adds the KMS driver for every Pi")
setup(no_kms + "\n[pi4]\narm_boost=1\n[all]\ndtoverlay=vc4-kms-v3d\n")
check(on("vc4-kms-v3d"), "a line under [all] after a filter counts")
setup(no_kms.replace("max_framebuffers=2", "  dtoverlay=vc4-kms-v3d,cma-128   # display\nmax_framebuffers=2"))
check(on("vc4-kms-v3d"), "with parameters, spaces and a comment it still counts")
setup(no_kms + "\n[all]\ndtoverlay=vc4-kms-v3d-pi4\n")
check(not on("vc4-kms-v3d"), "another overlay whose name starts the same doesn't count")
setup(STOCK + "\n[all]\ndtoverlay=waveshare-35dpi-3b-4b\n")
code, out, changed = lines("dpi35")
check(changed == "yes" and "already switched on" not in out and active(read(), "dtoverlay=waveshare-35dpi") == 1,
      "Waveshare's older waveshare-35dpi-3b-4b overlay is not taken for the current one")
setup(STOCK.replace("dtparam=audio=on", "dtparam=audio=on\n#dtoverlay=pitft28-resistive,rotate=90"))
check(not on("pitft28-resistive"), "a commented-out line doesn't count")

print("switching between the two small screens and HDMI")
setup(STOCK + OLD_PITFT)
code, out, changed = lines("pitft")
check(changed == "no" and read() == STOCK + OLD_PITFT, "a Pi set up by 0.9.1 for the PiTFT is already switched on")
code, out, changed = lines("dpi35")
text = read()
check(changed == "yes" and "pitft28" not in text and "spi=on\n" not in text.split("# z1screen", 1)[1],
      "the 0.9.1 PiTFT block is taken out")
check(active(text, "dtoverlay=waveshare-35dpi") == 1, "and the DPI screen switched on")
code, out, changed = lines("pitft")
text = read()
check(changed == "yes" and "waveshare" not in text and active(text, "dtoverlay=pitft28-resistive,rotate=90") == 1,
      "back to the PiTFT: the DPI block goes, the PiTFT block comes back")
check(text == STOCK + OLD_PITFT.replace("Z1 job screen", "BitCue"),
      "the same lines as 0.9.1's block, now saying the BitCue installer added them")
code, out, changed = lines("none")
check(changed == "yes" and read() == STOCK, "a monitor on HDMI: config.txt is back to the original")
code, out, changed = lines("none")
check(changed == "no" and read() == STOCK, "and nothing changes on a second run")

print("lines someone added by hand are left alone")
setup(STOCK + "\n[all]\ndtoverlay=waveshare-35dpi\ndtoverlay=waveshare-touch-35dpi\n")
code, out, changed = lines("dpi35")
check(changed == "no" and "# z1screen" not in read() and "already switched on" in out,
      "a hand-added DPI overlay counts as switched on")
code, out, changed = lines("none")
check(changed == "no" and "dtoverlay=waveshare-35dpi" in read(), "and HDMI does not take it out")

print("a config.txt saved with Windows line endings")
crlf = (STOCK + OLD_PITFT + "dtparam=i2c_arm=on\n").replace("\n", "\r\n")
setup(crlf)
code, out, changed = lines("none")
check(code == 0 and changed == "yes" and read() == (STOCK + "dtparam=i2c_arm=on\n").replace("\n", "\r\n"),
      "the block comes out; the lines after it stay, with their own line endings")

print("a block that lost its end line")
broken = STOCK + "\n# z1screen: Waveshare 3.5inch DPI LCD (added by the BitCue installer)\n[all]\n" \
                 "dtoverlay=waveshare-35dpi\n\n# added later by hand\ndtparam=i2c_arm=on\n"
setup(broken)
check(run('if z1_blocks_closed "$1"; then echo CLOSED; else echo OPEN; fi', CFG)[1].startswith("OPEN"),
      "found before anything is changed")
for screen in ("none", "pitft", "dpi35"):
    code, out, changed = lines(screen)
    check(code != 0 and read() == broken and "no '# z1screen end' line" in out and not os.path.exists(CFG + ".z1tmp"),
          "%s: refused, and nothing after the block is lost" % screen)
setup(STOCK + OLD_PITFT)
check(run('if z1_blocks_closed "$1"; then echo CLOSED; else echo OPEN; fi', CFG)[1].startswith("CLOSED"),
      "a whole block is fine")

print("an unknown screen name")
setup(STOCK)
code, out, changed = lines("pitft35")
check(code != 0 and read() == STOCK, "refused, config.txt not touched")

print("getting and checking the overlay files")
kit = os.path.join(WORK, "kit")
ov = os.path.join(WORK, "overlays")
os.makedirs(kit)
os.makedirs(ov)
open(os.path.join(ov, "README"), "w").close()
stage = tempfile.mkdtemp(dir=WORK)
gone = "file://" + os.path.join(WORK, "nothing-here.zip")
env = {"TEST_PINS": pins(), "TEST_URL": gone}
stage_cmd = 'z1_stage_overlays "$1" "$2" "$3" "$4"; echo "STAGED=$STAGED"'
code, out, changed = run(stage_cmd, "pitft", kit, ov, stage)
check(code == 0 and "STAGED=no" in out, "the PiTFT has no overlay files to get")

code, out, changed = run(stage_cmd, "test", kit, ov, stage, env=env)
check(code != 0 and "Could not download" in out and "test-dtbo.zip" in out and os.listdir(ov) == ["README"],
      "no zip in the kit and no download: stops, with what to do, before anything is copied")

served = os.path.join(WORK, "served.zip")
dtbo_zip(served)
code, out, changed = run(stage_cmd, "test", kit, ov, stage, env=dict(env, TEST_URL="file://" + served))
check(code == 0 and "STAGED=yes" in out and os.listdir(ov) == ["README"], "downloaded and checked; nothing copied yet")
code, out, changed = run('z1_install_overlays "$1" "$2" "$3"', "test", stage, ov, env=env)
check(code == 0 and changed == "yes", "then copied, and a restart is asked for")
check(sorted(os.listdir(ov)) == ["README", "waveshare-35dpi.dtbo", "waveshare-touch-35dpi.dtbo"],
      "only the two files the screen needs")
check(open(os.path.join(ov, "waveshare-35dpi.dtbo"), "rb").read() == body("waveshare-35dpi.dtbo"),
      "with the zip's contents")
code, out, changed = run('z1_install_overlays "$1" "$2" "$3"', "test", stage, ov, env=env)
check(code == 0 and changed == "no", "the same files again: nothing copied")

code, out, changed = run(stage_cmd, "test", kit, ov, stage, env=env)
check(code == 0 and "already there" in out and "STAGED=no" in out,
      "the right files already there and no zip in the kit: nothing downloaded (the address doesn't exist)")
with open(os.path.join(ov, "waveshare-touch-35dpi.dtbo"), "wb") as fh:
    fh.write(b"older")
code, out, changed = run(stage_cmd, "test", kit, ov, stage, env=dict(env, TEST_URL="file://" + served))
check(code == 0 and "STAGED=yes" in out, "an older file there: the zip is fetched again")
code, out, changed = run('z1_install_overlays "$1" "$2" "$3"', "test", stage, ov, env=env)
check(changed == "yes" and "copied waveshare-touch-35dpi.dtbo" in out and "copied waveshare-35dpi.dtbo" not in out,
      "and only the file that differs is replaced")

dtbo_zip(os.path.join(kit, "test-dtbo.zip"))
code, out, changed = run(stage_cmd, "test", kit, ov, stage, env=env)
check(code == 0 and "STAGED=yes" in out, "a zip in the kit's top folder is used instead of downloading")

empty = os.path.join(WORK, "empty-overlays")
os.makedirs(empty)
for why, make, want in (
        ("a file that is not the tested version",
         lambda p: dtbo_zip(p, content=lambda n: b"changed " + n.encode()), "not the version this installer was tested with"),
        ("a zip without the touch overlay", lambda p: dtbo_zip(p, ("waveshare-35dpi.dtbo",)),
         "waveshare-touch-35dpi.dtbo is not in"),
        ("a download that is not a zip", lambda p: open(p, "w").write("<html>Not found</html>"), "Can't open")):
    make(os.path.join(kit, "test-dtbo.zip"))
    code, out, changed = run(stage_cmd, "test", kit, empty, stage, env=env)
    check(code != 0 and want in out and os.listdir(empty) == [], "refused: %s; nothing copied" % why)

dpi_pins = run('z1_screen_info dpi35 dtbos')[1].split("BOOT_CHANGED")[0].split()
check([p.split(":")[0] for p in dpi_pins] == ["waveshare-35dpi.dtbo", "waveshare-touch-35dpi.dtbo"]
      and all(re.match(r"^[0-9a-f]{64}$", p.split(":")[1]) for p in dpi_pins),
      "the 3.5inch DPI screen's two files are pinned by SHA-256")
dtbo_zip(os.path.join(kit, "3.5DPI-dtbo.zip"))
code, out, changed = run(stage_cmd, "dpi35", kit, empty, stage)
check(code != 0 and "not the version this installer was tested with" in out and os.listdir(empty) == [],
      "a 3.5DPI-dtbo.zip with other files than Waveshare's is refused")

print("is the screen there yet")
sysfs = os.path.join(WORK, "graphics")
found = 'if z1_fb_found "$1" "$2"; then echo FOUND; else echo MISSING; fi'
os.makedirs(sysfs)
check(run(found, "dpi35", sysfs)[1].startswith("MISSING"), "no framebuffer: not there yet (restart)")
for n, size in (("fb0", "1920,1080"), ("fb1", "320,240")):
    os.makedirs(os.path.join(sysfs, n))
    with open(os.path.join(sysfs, n, "virtual_size"), "w") as fh:
        fh.write(size + "\n")
check(run(found, "pitft", sysfs)[1].startswith("FOUND"), "the PiTFT's 320 x 240 found")
check(run(found, "dpi35", sysfs)[1].startswith("MISSING"), "a monitor and a PiTFT are not the 640 x 480 screen")
os.makedirs(os.path.join(sysfs, "fb2"))
with open(os.path.join(sysfs, "fb2", "virtual_size"), "w") as fh:
    fh.write("480,640\n")
check(run(found, "dpi35", sysfs)[1].startswith("FOUND"), "640 x 480 found, either way round")

print("the touch driver loaded at every boot")
MODS = os.path.join(WORK, "modules-load.d")
CONF = os.path.join(MODS, "z1screen-touch.conf")


def touch(screen):
    return run('z1_touch_autoload "$1" "$2"', MODS, screen)


code, out, _ = touch("pitft")
check(code == 0 and os.path.isfile(CONF), "PiTFT: a file in modules-load.d (made if missing)")
names = [l.strip() for l in read(CONF).splitlines() if l.strip() and not l.startswith("#")]
check(names == ["stmpe_ts"], "naming the STMPE610 touch driver, stmpe_ts, and nothing else: %s" % names)
before = read(CONF)
code, out, _ = touch("pitft")
check(code == 0 and read(CONF) == before and "already" in out, "running the installer again changes nothing")
with open(CONF, "w", encoding="utf-8") as fh:
    fh.write(before.replace("BitCue", "Z1 job screen"))
code, out, _ = touch("pitft")
check(code == 0 and read(CONF) == before and "(added by the BitCue installer)" in before,
      "the file an install from before the BitCue name wrote is written again")
with open(os.path.join(MODS, "someone-else.conf"), "w") as fh:
    fh.write("i2c-dev\n")
code, out, _ = touch("dpi35")
check(code == 0 and not os.path.exists(CONF), "switching to the 3.5inch DPI screen takes it out")
touch("pitft")
code, out, _ = touch("none")
check(code == 0 and not os.path.exists(CONF), "HDMI or removing the screen software: taken out")
check(read(os.path.join(MODS, "someone-else.conf")) == "i2c-dev\n", "other files there are left alone")
inst = read(INSTALL)
uninst = read(os.path.join(ROOT, "install", "uninstall.sh"))
check('z1_touch_autoload /etc/modules-load.d "$SCREEN"' in inst and 'modprobe "$TOUCH"' in inst and
      "z1_touch_autoload /etc/modules-load.d none" in inst, "the installer sets it for the screen, and loads it now")
check("z1_touch_autoload /etc/modules-load.d none" in uninst, "the uninstaller takes it out")

check(run('z1_screen_info pitft touch')[1].startswith("stmpe_ts") and
      run('z1_screen_info dpi35 touch')[1].startswith("BOOT_CHANGED"), "set in the table of screens")

print("a quiet boot: BitCue's splash instead of the boot text")
CMDLINE = os.path.join(WORK, "cmdline.txt")
NOTES = os.path.join(WORK, "etc-z1screen")
LISTED = os.path.join(NOTES, "cmdline-added.txt")
STOCK_CMDLINE = ("console=serial0,115200 console=tty1 root=PARTUUID=4e639091-02 rootfstype=ext4 fsck.repair=yes "
                 "rootwait cfg80211.ieee80211_regdom=US\n")
QUIET = ["quiet", "loglevel=3", "logo.nologo", "vt.global_cursor_default=0"]


def quiet(want, text=None):
    if text is not None:
        with open(CMDLINE, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        for p in (CMDLINE + ".before-z1screen", LISTED):
            if os.path.exists(p):
                os.remove(p)
    return run('z1_quiet_boot "$1" "$2" "$3"', CMDLINE, NOTES, want)


code, out, changed = quiet("on", STOCK_CMDLINE)
check(code == 0 and changed == "yes" and read(CMDLINE) == STOCK_CMDLINE.rstrip("\n") + " " + " ".join(QUIET) + "\n",
      "on the end of the one line: no boot messages, no raspberry logo, no blinking cursor")
check(read(LISTED).split() == QUIET and read(CMDLINE + ".before-z1screen") == STOCK_CMDLINE,
      "the options it added are listed in cmdline-added.txt, and the first change keeps a copy of the file")
code, out, changed = quiet("on")
check(code == 0 and changed == "no" and "already" in out and read(CMDLINE).count(" quiet") == 1,
      "running the installer again changes nothing")
code, out, changed = quiet("off")
check(code == 0 and changed == "yes" and read(CMDLINE) == STOCK_CMDLINE and not os.path.exists(LISTED),
      "off: the line is back as it was, byte for byte, and the list is gone")
own = STOCK_CMDLINE.replace(" rootwait", " rootwait quiet loglevel=7")
quiet("on", own)
check(read(LISTED).split() == ["logo.nologo", "vt.global_cursor_default=0"] and "loglevel=3" not in read(CMDLINE),
      "quiet and loglevel=7 set by hand stay as they are and aren't listed as the installer's")
code, out, changed = quiet("off")
check(read(CMDLINE) == own, "off takes out only what the installer added")
odd = STOCK_CMDLINE.replace("rootwait", "rootwait ").replace("\n", "\r\n")
code, out, changed = quiet("off", odd)
check(code == 0 and changed == "no" and read(CMDLINE) == odd, "nothing of the installer's there: the file isn't touched")
quiet("on", STOCK_CMDLINE.replace("\n", "\r\n"))
check(read(CMDLINE).count("\n") == 1 and "\r" not in read(CMDLINE), "a line with Windows line endings stays one line")
code, out, _ = run('z1_quiet_boot "$1" "$2" on', os.path.join(WORK, "missing.txt"), NOTES)
check(code != 0 and "no " in out, "no cmdline.txt: says so and changes nothing")
check('z1_quiet_boot "$BOOT/cmdline.txt" /etc/z1screen on' in inst and
      'z1_quiet_boot "$BOOT/cmdline.txt" /etc/z1screen off' in inst,
      "the installer sets it for the small screens and takes it out for an HDMI monitor")
check('z1_quiet_boot "$BOOT/cmdline.txt" /etc/z1screen off' in uninst and
      uninst.index("z1_quiet_boot") < uninst.index("rmdir /etc/z1screen"),
      "the uninstaller takes it out before it removes /etc/z1screen, where the list is")
unit = read(os.path.join(ROOT, "install", "z1screen-tft.service"))
check("DefaultDependencies=no" in unit and "After=local-fs.target" in unit and "WantedBy=sysinit.target" in unit
      and "Conflicts=shutdown.target" in unit and "systemctl reenable z1screen-tft.service" in inst,
      "the small screen's service starts early in the boot, so its splash replaces the boot text, and stops at shutdown")
check("OpenFile=/dev/tty1:tty1:graceful" in unit and "AmbientCapabilities=CAP_SYS_TTY_CONFIG" in unit,
      "systemd opens tty1 for the screen program (since Raspberry Pi OS 13 only root may open a console), and the "
      "program may switch it to graphics mode")

print("a screen mounted upside down (--flip)")
STATE = os.path.join(WORK, "state")
SCREEN_JSON = os.path.join(STATE, "screen.json")


def flip(want="yes"):
    return run('z1_flip_picture "$1" "$2"', STATE, want)


def screen_json():
    with open(SCREEN_JSON, encoding="utf-8") as fh:
        return json.load(fh)


setup(STOCK)
code, out, changed = flip()
check(code == 0 and screen_json() == {"flip": True} and "turned upside down" in out,
      "the settings page's switch is set, in screen.json in the state folder (made if missing)")
check(changed == "no" and read() == STOCK, "nothing in config.txt, and no restart needed")
code, out, changed = flip()
check(code == 0 and screen_json() == {"flip": True} and "already upside down" in out, "running it again changes nothing")
with open(SCREEN_JSON, "w", encoding="utf-8") as fh:
    json.dump({"clock_24h": True}, fh)
flip()
check(screen_json() == {"clock_24h": True, "flip": True}, "the page's other choices are kept")
code, out, changed = flip("no")
check(code == 0 and screen_json() == {"clock_24h": True, "flip": False} and "right way up again" in out,
      "--no-flip turns it back")
os.remove(SCREEN_JSON)
code, out, changed = flip("no")
check(code == 0 and screen_json() == {"flip": False},
      "--no-flip writes false even with no choice yet, so a z1screen.json saying true doesn't win")
with open(SCREEN_JSON, "w", encoding="utf-8") as fh:
    fh.write("{not json")
code, out, changed = flip()
check(code == 0 and screen_json() == {"flip": True}, "a damaged screen.json is replaced")
code, out, changed = flip("sideways")
check(code != 0 and screen_json() == {"flip": True}, "anything but yes or no is refused, changing nothing")

# The services read it: the settings page's choice wins over a z1screen.json made from config.example.json.
example = json.load(open(os.path.join(ROOT, "config.example.json"), encoding="utf-8"))
check(example["tft"]["flip"] is False, "config.example.json says flip: false (so the installer can't set it there)")
cfg = config._merge(copy.deepcopy(config.DEFAULTS), example)
hub = Hub(cfg, JobStore(STATE, [make_library((SIGN,))]))
check(hub.screen_info()["flip"] is True, "the screen service reports the picture upside down")

# Touches: raw readings as each screen's touch chip gives them, for a spot on the glass (in the panel's own
# 320 x 240 or 640 x 480 pixels). Upside down, a dot drawn at x, y is on the glass at the opposite corner.
raw_pitft = lambda gx, gy: (3900 - gy * 14.5, 250 + gx * 11.2)     # STMPE610: X and Y swapped, Y turned round
raw_goodix = lambda gx, gy: (gx, gy)                                 # Goodix: the panel's pixels
for name, raw, k in (("PiTFT", raw_pitft, 1), ("3.5inch DPI", raw_goodix, 2)):
    def glass(x, y, down):
        return ((touch_mod.W - 1 - x) * k, (touch_mod.H - 1 - y) * k) if down else (x * k, y * k)

    spots = [(20, 30), (160, 120), (300, 220)]
    cal_down = touch_mod.calibrate([raw(*glass(x, y, True)) for x, y in touch_mod.CAL_POINTS], flip=True)
    cal_up = touch_mod.calibrate([raw(*glass(x, y, False)) for x, y in touch_mod.CAL_POINTS], flip=False)
    for why, cal in (("set up upside down", cal_down), ("set up the right way up, then flipped", cal_up)):
        got = [touch_mod.apply(cal, *raw(*glass(x, y, True)), True) for x, y in spots]
        check(all(abs(px - x) < 1.5 and abs(py - y) < 1.5 for (x, y), (px, py) in zip(spots, got)),
              "%s, touch %s: a touch on a button lands on it" % (name, why))

print("the installer's steps")
src = open(INSTALL, encoding="utf-8").read()
copying = src.index('"==> Copying the screen software')
check(src.index("z1_blocks_closed") < copying and src.index("z1_stage_overlays") < copying,
      "config.txt is checked and the overlay files fetched before anything on the Pi is changed")
check(src.index("z1_stage_overlays") < src.index("z1_install_overlays"), "the files are copied only after they are checked")
pil = next(l for l in src.splitlines() if "python3-pil" in l and "apt-get install" in l)
check("curl" not in pil, "no extra package for the download (Python fetches it)")
p = subprocess.run(["bash", INSTALL, "--dpi"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
check(p.returncode == 1 and "--dpi35" in p.stdout, "an unknown option lists --dpi35")
if os.geteuid() != 0:
    p = subprocess.run(["bash", INSTALL, "--dpi35", "--no-reboot"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True)
    check(p.returncode == 1 and "Unknown option" not in p.stdout and "sudo" in p.stdout,
          "--dpi35 is accepted (stops at the sudo check here)")
    for flags in (["--flip"], ["--dpi35", "--flip"], ["--no-flip"]):
        p = subprocess.run(["bash", INSTALL, "--no-reboot"] + flags, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True)
        check(p.returncode == 1 and "Unknown option" not in p.stdout and "sudo" in p.stdout,
              "%s is accepted (stops at the sudo check here)" % " ".join(flags))
for flags in (["--hdmi", "--flip"], ["--no-flip", "--hdmi"]):
    p = subprocess.run(["bash", INSTALL] + flags, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    check(p.returncode == 1 and "monitor's own menu" in p.stdout, "%s: refused, with what to do" % " ".join(flags))
check(src.index("z1_flip_picture") < src.index("systemctl restart z1screen.service"),
      "the flip is set before the screen services start again (they read screen.json at start)")
check("chown z1screen:z1screen /var/lib/z1screen /var/lib/z1screen/screen.json" in src,
      "screen.json belongs to the services' user, so the settings page's switch can still change it")
check("rotate=270" not in src and all("z1_quiet_boot" in l or "-f \"$BOOT/cmdline.txt\"" in l
                                      for l in src.splitlines() if "cmdline.txt" in l),
      "the flip changes no boot files (cmdline.txt gets only the quiet-boot options)")

shutil.rmtree(WORK)
print("all installer checks passed")
