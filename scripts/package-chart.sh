#!/usr/bin/env bash
# Package chart/colibri into dist/colibri-<version>.tgz for Market > My Olares > Upload custom app package.
set -euo pipefail
cd "$(dirname "$0")/.."
V=$(awk '/^version:/{print $2; exit}' chart/colibri/Chart.yaml)
mkdir -p dist
tar -C chart -czf "dist/colibri-${V}.tgz" colibri
echo "dist/colibri-${V}.tgz"
