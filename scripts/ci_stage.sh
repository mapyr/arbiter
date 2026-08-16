#!/usr/bin/env bash
# Copy tests into an empty directory so pytest imports the *installed* package,
# not the git checkout. Extra paths (e.g. scripts/) are copied into DEST as-is.
#
# Usage: scripts/ci_stage.sh DEST [extra_path ...]
set -euo pipefail

dest="${1:?destination directory}"
shift
mkdir -p "$dest"
cp -R tests "$dest/tests"
cp arbiter.rules.yaml.example "$dest/"
for extra in "$@"; do
  cp -R "$extra" "$dest/"
done
printf '%s\n' "$dest"
