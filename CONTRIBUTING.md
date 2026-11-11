# Contributing

When you report a bug, include your Z1 model and firmware version, your Makera
Studio version, your Pi and screen, and what `/debug` shows.

To run the tests:

```
python3 -m pip install pillow
bash tests/run_all.sh
```

The tests use a simulated Z1 and made-up jobs (`tools/samples.py`).

Rules for changes:

- Nothing may depend on one person's machine, plate, bits or file names.
- The Pi must never stop a job or confirm a tool change.
- Don't add packages.
- Add a test for each change.

- Don't add 3D model files (STL, 3MF, STEP and the like). The housing's files are sold separately and
  are not part of this repository.

Changes are released under GPL-3.0-or-later.

For questions rather than changes, see [SUPPORT.md](SUPPORT.md).
