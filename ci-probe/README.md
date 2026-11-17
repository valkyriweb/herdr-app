# Disposable Linux scan-stall measurement

This branch does not target a merge. It adds only probe files and a push-triggered Linux workflow. Production PR2 remains at d6f008e3277f9beaecbf50bafec72e9b43acf6d9 with its three-platform CI checks unchanged. Existing ci.yml is unchanged and runs neither macOS nor Windows for this probe branch.

## Reproduction

The workflow reuses ci.yml toolchain pins, dependency installation and Linux cache. It downloads public core0.1.21 using its pinned SHA256 and versioned manifest. It builds the exact baseline321c9a84bc928f1c31be59e73dad966d86a3e252 and fixed d6f008e3277f9beaecbf50bafec72e9b43acf6d9 source payloads with the same dev profile. Guards reject unrelated source differences or initial tracked modifications. Only this disposable checkout is temporarily swapped; saved own source files are restored after building. Binary/source hashes are recorded.

The native measurement refuses to run outside a disposable Linux GitHub Actions runner. It reuses scripts/sandbox through a private copy with an explicit prebuilt-binary check in place of cargo build. Ubuntu22.04 uses the full Weston backend module name. The original sandbox is unchanged. Each run has a unique scratch home, core session, configuration directory, Weston display and XDG runtime directory. No real app, core or display is contacted.

Three paired trials cover plain child, synthetic blocked-question display, and6000lines of synthetic main-screen output. Trial order is baseline/fixed, fixed/baseline, baseline/fixed within each case. Every fixture is newly started. No case is a real Pi session, actual async_ask, model transcript, or old session. No private question code or data is included.

After startup, an unused mod manifest becomes a FIFO. Successful nonblocking writer-open proves an active reader. EOF is held2s before publishing a regular manifest for future scans and completing the current FIFO read. Both timed requests must start during the confirmed hold. The open phase times status and terminal.peek with screenshot capture. The update phase triggers an independent fixture color change during a second hold. Status is read back and must name the expected space without errors. The terminal modal must remain present. Screenshots must contain at least1000 exact pixels of the child-specific magenta or update-specific green marker; a pre-open screenshot must not contain the child marker. Missing prerequisites, incorrect states or failed cleanup abort rather than becoming a performance PASS.

## Results

- build.json/core.json: source revisions, payload/binary/release hashes and toolchain.
- observations/environment.json: host, CPU count and interpretation limits.
- observations/results.jsonl: one row per variant/case/trial. Individual case folders also contain result.json, screenshots, fixture files and app/display logs.
- metrics.open/update.status.elapsed_ms: CLI round-trip through the GPUI thread, not first paint.
- metrics.open/update.visible.command_ms: modal-command acknowledgment or fixture-update trigger.
- metrics.open/update.visible.screenshot_capture_upper_bound_ms: time through native screenshot completion whose content was subsequently verified. This includes screenshot RPC/encoding. It excludes offline pixel decoding, reported separately as pixel_verification_ms. It is not a physical presentation timestamp.
- hold.reader_acquired_ns/writer_released_ns: synchronization evidence; all stage timestamps use the same monotonic clock on the runner.
- app_rss_kb: one post-run RSS observation, not proof of memory growth.

There is no arbitrary timing threshold. Successful execution establishes measurement coverage and state checks, not that the user-reported cause is fixed. Inspect the paired numbers. The artificial2s blocked read proves causality for that injected filesystem stall only; it does not establish real-world stall frequency, actual question cost, or session-age effects.

Local static check only (launches no app/core/UI):

```sh
bash -n ci-probe/build-pair.sh ci-probe/fetch-core.sh
python3 ci-probe/scan_stall_probe.py --self-check
```
