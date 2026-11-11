"""install-to-pi.sh's copy of the kit: no Mac file attributes in it, so the Pi's tar doesn't print a warning
for every file. The archive is made with the script's own tar line, on a folder whose files carry an
attribute (on a Mac, the way downloaded files do); nothing is sent anywhere.
Then its questions when double-clicked (which screen, upside down or not), answered through a stand-in ssh that
only notes the installer line it would run on the Pi."""
import os, re, shutil, subprocess, sys, tempfile
from common import ROOT, check

SCRIPT = open(os.path.join(ROOT, "install-to-pi.sh"), encoding="utf-8").read()
line = next(l for l in SCRIPT.splitlines() if re.search(r"\btar -C \"\$DIR\".*-czf -", l))

print("the copy leaves Mac file attributes out")
check("--no-xattrs" in line.split(), "the kit is packed with --no-xattrs")
check("COPYFILE_DISABLE=1" in line, "and without Mac resource files (COPYFILE_DISABLE=1)")

if sys.platform == "darwin" and shutil.which("xattr"):
    work = tempfile.mkdtemp()
    kit = os.path.join(work, "kit")
    os.makedirs(os.path.join(kit, "z1screen"))
    path = os.path.join(kit, "z1screen", "a.py")
    with open(path, "w") as fh:
        fh.write("x = 1\n")
    subprocess.run(["xattr", "-w", "com.example.bitcue-test", "1", path], check=True)

    def packed(flags):
        tar = subprocess.run(["tar", "-C", kit] + flags + ["-cf", "-", "z1screen"], stdout=subprocess.PIPE,
                             env=dict(os.environ, COPYFILE_DISABLE="1"), check=True)
        return tar.stdout

    check(b"LIBARCHIVE.xattr" in packed([]), "a file with an attribute is packed with it by default (the old copy)")
    check(b"LIBARCHIVE.xattr" not in packed(["--no-xattrs"]), "with --no-xattrs it is not")
    shutil.rmtree(work)
else:
    print("  skipped - packing a file with an attribute (needs a Mac)")

print("double-clicked, it asks which screen and whether it hangs upside down")
bin_dir = tempfile.mkdtemp()
calls = os.path.join(bin_dir, "calls.txt")
for name, body in (("ssh", 'echo "$*" >> "%s"; cat > /dev/null' % calls), ("ssh-keygen", ""), ("curl", ""),
                   ("open", ""), ("xdg-open", "")):
    with open(os.path.join(bin_dir, name), "w") as fh:
        fh.write("#!/bin/sh\n%s\nexit 0\n" % body)
    os.chmod(os.path.join(bin_dir, name), 0o755)


def helper(answers, *args):
    """Runs install-to-pi.sh with the stand-in ssh; returns (exit code, output, the installer line it ran)."""
    open(calls, "w").close()
    run = subprocess.run(["bash", os.path.join(ROOT, "install-to-pi.sh")] + list(args), input=answers,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60,
                         env=dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"]))
    lines = [l for l in open(calls).read().splitlines() if "install/install.sh" in l]
    return run.returncode, run.stdout, (lines[0] if lines else "")


code, out, line = helper("\n\npi\nz1screen\n")
check(code == 0 and "Which screen" in out, "it asks which screen is on the Pi")
check(line.endswith("install.sh --dpi35 --flip") and "pi@z1screen.local" in line,
      "Return, Return: the Waveshare screen, upside down in the housing (%s)" % line)
code, out, line = helper("2\nn\npi\nshop\n")
check(line.endswith("install.sh --tft --no-flip") and "pi@shop.local" in line, "2, n: the PiTFT, the right way up (%s)" % line)
code, out, line = helper("3\npi\n\n")
check(line.endswith("install.sh --hdmi") and "pi@z1screen.local" in line,
      "3: a monitor, and no question about the housing: the next answer is the user name (%s)" % line)
code, out, line = helper("", "--dpi35", "pi@z1screen.local")
check(code == 0 and "Which screen" not in out and line.endswith("install.sh --dpi35"),
      "a user@host and a screen option skip the questions (%s)" % line)
code, out, line = helper("7\n")
check(code != 0 and not line and not open(calls).read(), "a wrong answer stops before connecting")
shutil.rmtree(bin_dir)

print("all copy checks passed")
