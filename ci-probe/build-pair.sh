#!/usr/bin/env bash
# Build the BASELINE and FIXED herdr-app from exact source payloads, with the
# same cargo profile, and keep both binaries and their hashes.
#
#   build-pair.sh <output-dir>      (run at the repo root of a full-history checkout)
#
# FIXED is the checkout itself; it must equal FIXED_SHA outside the probe's own
# files. BASELINE is FIXED with only src/window.rs and
# src/window/folder_scan_tests.rs taken from BASE_SHA, which is exactly the
# difference between the two commits (checked below). The working tree is
# restored afterwards.
set -euo pipefail

BASE_SHA=${BASE_SHA:-321c9a84bc928f1c31be59e73dad966d86a3e252}
FIXED_SHA=${FIXED_SHA:-d6f008e3277f9beaecbf50bafec72e9b43acf6d9}
PROBE_DIR=${PROBE_DIR:-ci-probe}
out=$(mkdir -p "${1:?usage: build-pair.sh <output-dir>}" && cd "$1" && pwd)
files=(src/window.rs src/window/folder_scan_tests.rs)

fail() { echo "build-pair: $*" >&2; exit 1; }
git cat-file -e "$BASE_SHA^{commit}" 2>/dev/null || fail "BASE_SHA $BASE_SHA is not in this checkout (needs fetch-depth: 0)"
git cat-file -e "$FIXED_SHA^{commit}" 2>/dev/null || fail "FIXED_SHA $FIXED_SHA is not in this checkout (needs fetch-depth: 0)"

# 1. The two commits differ in exactly the two files.
changed=$(git diff --name-only "$BASE_SHA" "$FIXED_SHA" | sort | tr '\n' ' ')
[ "$changed" = "$(printf '%s\n' "${files[@]}" | sort | tr '\n' ' ')" ] || fail "BASE..FIXED changes unexpected files: $changed"
# 2. The checkout is FIXED, apart from the probe's own files and the workflow patch.
git diff --quiet "$FIXED_SHA" HEAD -- . ":(exclude)$PROBE_DIR" ":(exclude).github/workflows" || fail "checkout differs from FIXED_SHA outside $PROBE_DIR and .github/workflows"
[ -z "$(git status --porcelain --untracked-files=no -- . ":(exclude)$PROBE_DIR" ":(exclude).github/workflows")" ] || fail "tracked files are modified"

export CARGO_PROFILE_DEV_DEBUG=1 CARGO_INCREMENTAL=0
build() {
  cargo build --locked --bin herdr-app --config 'profile.dev.package."*".opt-level=0' 2>&1 | tee "$out/build-$1.log" | tail -n 10
  cp target/debug/herdr-app "$out/herdr-app-$1"
}
mkdir -p "$out/source-fixed"
cp src/window.rs "$out/source-fixed/window.rs"
cp src/window/folder_scan_tests.rs "$out/source-fixed/folder_scan_tests.rs"
restore() {
  # Restore only this script's own swap in this verified clean disposable checkout.
  cp "$out/source-fixed/window.rs" src/window.rs
  cp "$out/source-fixed/folder_scan_tests.rs" src/window/folder_scan_tests.rs
}
trap restore EXIT

echo "== build fixed ($FIXED_SHA)"
build fixed

echo "== build baseline ($BASE_SHA)"
[ -z "$(git status --porcelain -- "${files[@]}")" ] || fail "source changed during fixed build"
git show "$BASE_SHA:src/window.rs" > src/window.rs
rm src/window/folder_scan_tests.rs
[ -z "$(git diff --name-only "$BASE_SHA" -- src crates Cargo.toml Cargo.lock vendor 2>/dev/null)" ] || fail "baseline tree is not BASE_SHA's tree"
build baseline
restore
trap - EXIT
git diff --exit-code -- "${files[@]}"

python3 - "$out" "$BASE_SHA" "$FIXED_SHA" <<'PY'
import hashlib, json, os, subprocess, sys
out, base, fixed = sys.argv[1:4]
sha = lambda b: hashlib.sha256(b).hexdigest()
def show(c, p):
    r = subprocess.run(["git", "show", f"{c}:{p}"], capture_output=True)
    return sha(r.stdout) if r.returncode == 0 else None
rec = {
    "base_sha": base, "fixed_sha": fixed,
    "head_sha": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
    "cargo_profile": "dev profile, dependencies at opt-level 0 (--config), CARGO_PROFILE_DEV_DEBUG=1, CARGO_INCREMENTAL=0, --locked --bin herdr-app",
    "rustc": subprocess.run(["rustc", "-V"], capture_output=True, text=True).stdout.strip(),
    "source_diff_sha256": sha(subprocess.run(["git", "diff", base, fixed], capture_output=True).stdout),
    "payload_sha256": {p: {"baseline": show(base, p), "fixed": show(fixed, p)} for p in ("src/window.rs", "src/window/folder_scan_tests.rs")},
    "binaries": {v: {"path": f"herdr-app-{v}", "sha256": sha(open(os.path.join(out, f"herdr-app-{v}"), "rb").read()),
                     "bytes": os.path.getsize(os.path.join(out, f"herdr-app-{v}"))} for v in ("baseline", "fixed")},
}
assert rec["binaries"]["baseline"]["sha256"] != rec["binaries"]["fixed"]["sha256"], "binaries are identical: the swap did not take effect"
json.dump(rec, open(os.path.join(out, "build.json"), "w"), indent=1)
print(json.dumps(rec["binaries"], indent=1))
PY
