#!/bin/bash
# Double-click this file on the Mac to put BitCue on the Raspberry Pi.
# It runs install-to-pi.sh from this folder in a Terminal window.
cd "$(dirname "$0")" || exit 1
bash install-to-pi.sh
echo
read -r -p "Press Return to close this window. " _
