"""Make job profiles for a kit's library/ folder from G-code files on this computer.

The Pi learns a job file's timing and cut area by itself when the file passes
through it or when it reads the file from the machine. A kit made for one
machine can carry the profiles of its job files instead, so the screen knows
them from the start (the release's library/ is empty), with the picture of each
file's part (<md5>.png, needs Pillow; --no-pictures leaves them out).

  python3 tools/build_library.py [--out DIR] <folder on the machine> <file.nc>...
Example: the files of ~/jobs/brackets, which are in /sd/gcodes/brackets on the machine's card:
  python3 tools/build_library.py /sd/gcodes/brackets ~/jobs/brackets/*.nc
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from z1screen import gcode, preview  # noqa

def main():
    args = sys.argv[1:]
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library")
    pictures = "--no-pictures" not in args
    args = [a for a in args if a != "--no-pictures"]
    if len(args) >= 2 and args[0] == "--out":
        out_dir, args = args[1], args[2:]
    if len(args) < 2 or not args[0].startswith("/sd/"):
        sys.exit(__doc__)
    folder = args[0].rstrip("/")
    os.makedirs(out_dir, exist_ok=True)
    for f in args[1:]:
        data = open(f, "rb").read()
        prof = gcode.analyze(data, folder + "/" + os.path.basename(f))
        with open(os.path.join(out_dir, prof["md5"] + ".json"), "w") as fh:
            json.dump(prof, fh)
        png = preview.render(data) if pictures and preview.available() else None
        if png:
            with open(os.path.join(out_dir, prof["md5"] + ".png"), "wb") as fh:
                fh.write(png)
        t = prof["total"]
        print("%-58s %2d changes  %d:%02d" % (os.path.basename(f), len(prof["changes"]), t // 3600, (t % 3600) // 60))

if __name__ == "__main__":
    main()
