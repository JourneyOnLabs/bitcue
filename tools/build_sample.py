"""Make the built-in demo job's profile, z1screen/sample-job.json, from tools/samples.py.

  python3 tools/build_sample.py

The demo on the settings page and the demo page (tools/build_demo.py) play this
made-up sign job. Run this again after changing samples.py or the time model
(gcode.py); tests/test_demo.py checks that the file is up to date.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from z1screen import demo, gcode  # noqa: E402
import samples  # noqa: E402

SAMPLE_PATH = "/sd/gcodes/samples/sample-sign.nc"


def build() -> dict:
    return gcode.analyze(samples.sign(), SAMPLE_PATH)


def main() -> None:
    prof = build()
    with open(demo.SAMPLE_FILE, "w", encoding="utf-8") as fh:
        json.dump(prof, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    t = prof["total"]
    print("wrote %s: %d tool changes, %d min" % (demo.SAMPLE_FILE, len(prof["changes"]), round(t / 60)))


if __name__ == "__main__":
    main()
