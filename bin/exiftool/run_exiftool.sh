#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXIF="$DIR/exiftool"
LIB="$DIR/lib"

if [ ! -f "$EXIF" ]; then
  echo "ERROR: bundled exiftool not found at $EXIF" >&2
  exit 2
fi

export PERL5LIB="$LIB${PERL5LIB:+:$PERL5LIB}"
exec perl "$EXIF" "$@"
