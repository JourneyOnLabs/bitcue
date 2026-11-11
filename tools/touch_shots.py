"""Pictures of the touch-screen flow, for the README.

  python3 tools/touch_shots.py [out_dir]

Runs the end-to-end touch test (tests/run_touch.py: the real drawing loop, a pretend touch layer and
PiTFT, the screen service and a pretend Z1 with the made-up sample jobs) with a scratch folder for its
pictures (and the settings address shown as z1screen.local:8080, as on a Pi), then copies them to
docs/screens/touch/ (or out_dir) under plain names, with sheet.png showing them all at twice the size and
a caption under each. Needs Pillow. The test's checks still run: if one fails, nothing is written.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from z1screen import tft  # noqa: E402
from tft_shots import sheet  # noqa: E402

# the test's picture names (without their number), in the order it takes them, and a caption for each
CAPTIONS = {
    "ready-set-up-touch": "First touch: set up the touch screen",
    "ready-jobs": "Ready, with the JOBS button",
    "folders": "JOBS: folders on the Z1",
    "files": "A folder's files (not read yet)",
    "reading": "Reading the file from the Z1",
    "place-suggested": "Place it: the Pi's suggested spot",
    "place-overlap": "Dragged onto a cut area: overlap",
    "place-arrows": "Moved clear with the arrows",
    "check": "Check the plate",
    "review": "Review, then hold START",
    "job-probe-stop": "Job started: load the probe",
    "spots-used-plate": "Layout spots, two already cut",
    "starting-off": "JOBS with starting turned off",
}


# runs the test with the address the screens show set as on a real Pi (not this computer's test port)
RUN = """
import os, runpy, sys
script, out = sys.argv[1], sys.argv[2]
sys.path[:0] = [os.path.dirname(script), os.path.dirname(os.path.dirname(script))]
from z1screen.hub import Hub
Hub.settings_url = lambda self: "http://z1screen.local:8080/settings"
sys.argv = [script, out]
runpy.run_path(script, run_name="__main__")
"""


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screens", "touch")
    tmp = tempfile.mkdtemp(prefix="z1touch-shots-")
    try:
        r = subprocess.run([sys.executable, "-B", "-c", RUN, os.path.join(ROOT, "tests", "run_touch.py"), tmp],
                           cwd=os.path.join(ROOT, "tests"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True)
        if r.returncode != 0:
            sys.stdout.write("\n".join(l for l in r.stdout.splitlines() if " INFO " not in l)[-4000:] + "\n")
            sys.exit("tests/run_touch.py failed; no pictures written")
        taken = []
        for f in sorted(os.listdir(tmp)):
            m = re.match(r"^\d+-(.+)\.png$", f)
            if m:
                taken.append((m.group(1), os.path.join(tmp, f)))
        if not taken:
            sys.exit("tests/run_touch.py made no pictures")
        os.makedirs(out_dir, exist_ok=True)
        for f in os.listdir(out_dir):          # old pictures, e.g. "reading" when the read was too quick to see
            if f.endswith(".png"):
                os.remove(os.path.join(out_dir, f))
        from PIL import Image
        pics = []
        for name, src in taken:
            shutil.copyfile(src, os.path.join(out_dir, name + ".png"))
            img = Image.open(src).convert("RGB")
            pics.append((CAPTIONS.get(name, name.replace("-", " ").capitalize()), img))
        sheet(pics, os.path.join(out_dir, "sheet.png"), tft.Fonts())
        print("wrote", len(pics), "pictures and sheet.png to", out_dir)
        for name, _ in taken:
            print("  %s.png" % name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
