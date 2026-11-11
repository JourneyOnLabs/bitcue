#!/bin/bash
# Runs every test, against a pretend Z1 on this computer (no machine, Pi or screen needed).
#   bash tests/run_all.sh            all suites
#   bash tests/run_all.sh run_sim    only the suites whose names contain run_sim
# Needs Python 3.8 or newer. The small-screen suites need Pillow (pip install pillow); without it
# they are skipped. Exits with 1 if any suite fails.
cd "$(dirname "$0")" || exit 1
set -o pipefail
PY="${PYTHON:-python3}"
if command -v timeout >/dev/null 2>&1; then TO="timeout 600"; elif command -v gtimeout >/dev/null 2>&1; then TO="gtimeout 600"; else TO=""; fi
HAVE_PIL=yes
"$PY" -c "from PIL import Image, ImageDraw, ImageFont" 2>/dev/null || HAVE_PIL=no
SUITES="test_units.py test_install.py test_notify.py test_bitlife.py test_copy_to_pi.py test_release.py test_preview.py test_plate_warning.py test_jobs.py test_demo.py test_tft.py run_sim.py run_relay_extras.py run_direct_fallback.py
run_stops.py run_polite.py run_settings.py run_tft.py run_pi_restart.py run_start.py run_touch.py run_previews.py run_bitlife.py"
NEEDS_PIL="test_tft.py run_tft.py run_touch.py test_preview.py run_previews.py test_plate_warning.py"
failed=""
passed=0
skipped=0
for t in $SUITES; do
  if [ -n "${1:-}" ] && [[ "$t" != *"$1"* ]]; then continue; fi
  if [ "$HAVE_PIL" = no ] && [[ " $NEEDS_PIL " == *" $t "* ]]; then
    echo "== $t  (skipped: needs Pillow)"
    skipped=$((skipped + 1))
    continue
  fi
  echo "== $t"
  log="$(mktemp)"
  if $TO "$PY" -B "$t" >"$log" 2>&1; then
    grep -v " INFO " "$log" | grep -E "ok -|skipped -|passed" || true
    passed=$((passed + 1))
  else
    grep -v " INFO " "$log" | tail -40
    echo "!! $t FAILED"
    failed="$failed $t"
  fi
  rm -f "$log"
done
echo
if [ -n "$failed" ]; then
  echo "FAILED:$failed  ($passed passed, $skipped skipped)"
  exit 1
fi
echo "All $passed suites passed ($skipped skipped)."
