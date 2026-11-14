#!/usr/bin/env bash
# Build and push the image by hand (the release workflow does this on version tags).
#   scripts/release-image.sh 1.0.0 [ghcr.io/OWNER/mailstudio]
set -euo pipefail
version=${1:?Usage: scripts/release-image.sh VERSION [IMAGE]}
image=${2:-ghcr.io/mailstudio/mailstudio}
major=${version%%.*}
minor=${version%.*}
cd "$(dirname "$0")/.."
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --file server/Dockerfile \
  --tag "$image:$version" --tag "$image:$minor" --tag "$image:$major" --tag "$image:latest" \
  --push .
