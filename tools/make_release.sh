#!/bin/bash
# Builds the release files in dist/ from this folder:
#   z1screen-<version>.zip   for people who download the kit to a computer (Mac helper, or copy to the Pi)
#   z1screen.tar.gz          the same files under z1screen/, for the one-line install on the Pi
# Packs only the files git tracks, as they are in this folder: untracked and ignored files (local notes,
# .venv, tests/out, dist) never go in, so add any new file to git first. Run the tests first: bash tests/run_all.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "make_release.sh packs the files git tracks, so it needs a git checkout of BitCue."
  exit 1
fi
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' z1screen/__init__.py)"
STAGE="$(mktemp -d)"
mkdir -p "$STAGE/z1screen" dist
# a tracked file deleted here and not yet committed is left out
git ls-files -z | while IFS= read -r -d '' f; do [ -f "$f" ] && printf '%s\0' "$f"; done \
  | COPYFILE_DISABLE=1 tar --no-xattrs --null -T - -cf - | tar -C "$STAGE/z1screen" -xf -
rm -f "dist/z1screen-$VERSION.zip" dist/z1screen.tar.gz
( cd "$STAGE" && cp -R z1screen "z1screen-$VERSION" && zip -qr "$ROOT/dist/z1screen-$VERSION.zip" "z1screen-$VERSION" )
COPYFILE_DISABLE=1 tar --no-xattrs -C "$STAGE" -czf dist/z1screen.tar.gz z1screen
rm -rf "$STAGE"
ls -l dist
echo "Release $VERSION: attach both files to the GitHub release v$VERSION."
