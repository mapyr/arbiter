#!/usr/bin/env bash
# Copy tests into an empty directory so pytest imports the *installed* package,
# not the git checkout. Always includes scripts/ (hexagon unit test) and
# pyproject.toml (pytest markers).
#
# Usage: scripts/ci_stage.sh DEST
set -euo pipefail

dest="${1:?destination directory}"
mkdir -p "$dest"
cp -R tests "$dest/tests"
cp -R scripts "$dest/scripts"
cp arbiter.rules.yaml.example "$dest/"
cp pyproject.toml "$dest/"
printf '%s\n' "$dest"
