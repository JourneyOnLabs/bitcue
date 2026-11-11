# Releasing

First time:

1. Name: BitCue. Repo: github.com/journeyonlabs/bitcue.
2. Turn on GitHub Pages for `docs/` (branch `main`, folder `/docs`) and private vulnerability reporting. The
   website is journeyonlabs.github.io/bitcue: `docs/index.md` is its front page, and the README's demo link
   plays `docs/demo.html` there.

Each release:

1. Set the version in `z1screen/__init__.py` and add it to CHANGELOG.md.
2. Run `bash tests/run_all.sh`. All suites must pass.
3. Run `python3 tools/tft_shots.py`, `python3 tools/touch_shots.py`, `python3 tools/web_shots.py` and `python3 tools/build_demo.py`.
4. Run `bash tools/make_release.sh`. It packs only the files git tracks, as they are in the folder, so `git add`
   any new file first; untracked and ignored files never go in.
5. Tag `v<version>`. Create a GitHub release and attach both files from `dist/`.
6. Test on a real Pi and Z1 before you announce it.
