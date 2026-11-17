#!/usr/bin/env bash
# Download a PINNED public herdr-core release for linux-x86_64, verify its
# sha256 against both a constant pinned here and the release's own
# versioned manifest, and unpack it. No credentials. Writes <dir>/core.json.
#
#   fetch-core.sh <output-dir>
#
# Output: <dir>/bin/herdr-core and <dir>/bin/hc (symlink), <dir>/core.json.
# Pin source: https://releases.herdr.dev/core/latest.json read 2026-11-17
# (version 0.1.21); the tarball hash was recomputed from the download.
set -euo pipefail

VERSION=0.1.21
SHA256=78424755c10a40dde38a0bdb55c20ac19706e9ae05ed68fc2acf1fcff0d941c1
URL=https://releases.herdr.dev/core/$VERSION/herdr-core-linux-x86_64.tar.gz
MANIFEST=https://releases.herdr.dev/core/$VERSION/manifest.json

out=${1:?usage: fetch-core.sh <output-dir>}
[ "$(uname -s)-$(uname -m)" = Linux-x86_64 ] || { echo "fetch-core: linux-x86_64 only, got $(uname -s)-$(uname -m)" >&2; exit 2; }
mkdir -p "$out/bin" "$out/dl"
curl -fsSL --retry 3 --retry-delay 2 --max-time 120 "$MANIFEST" -o "$out/dl/manifest.json"
python3 - "$out/dl/manifest.json" "$VERSION" "$SHA256" "$URL" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
t = m["targets"]["linux-x86_64"]
assert m["version"] == sys.argv[2], f"manifest version {m['version']} != pinned {sys.argv[2]}"
assert t["sha256"] == sys.argv[3], f"manifest sha256 {t['sha256']} != pinned {sys.argv[3]}"
assert t["url"] == sys.argv[4], f"manifest url {t['url']} != pinned {sys.argv[4]}"
PY
curl -fsSL --retry 3 --retry-delay 2 --max-time 300 "$URL" -o "$out/dl/core.tar.gz"
got=$(sha256sum "$out/dl/core.tar.gz" | cut -d' ' -f1)
[ "$got" = "$SHA256" ] || { echo "fetch-core: checksum mismatch: got $got want $SHA256" >&2; exit 1; }
tar -xzf "$out/dl/core.tar.gz" -C "$out/dl" herdr-core
install -m 755 "$out/dl/herdr-core" "$out/bin/herdr-core"
ln -sf herdr-core "$out/bin/hc"
reported=$("$out/bin/hc" --version)
case $reported in "hc $VERSION"*) ;; *) echo "fetch-core: unexpected version: $reported" >&2; exit 1 ;; esac
python3 - "$out/core.json" "$VERSION" "$SHA256" "$URL" "$reported" "$(sha256sum "$out/bin/herdr-core" | cut -d' ' -f1)" <<'PY'
import json, sys
k = ("version", "tarball_sha256", "url", "reported_version", "binary_sha256")
json.dump(dict(zip(k, sys.argv[2:])), open(sys.argv[1], "w"), indent=1)
PY
echo "core $reported verified: $out/bin/hc"
