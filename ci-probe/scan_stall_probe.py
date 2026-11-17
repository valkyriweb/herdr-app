#!/usr/bin/env python3
"""Disposable Linux sandbox measurements. No model, real sessions or real display.

These synthetic main-screen terminal cases are NOT Pi pending-question or age proof.
Native screenshot completion is a latency upper bound, not physical first paint.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import time
import zlib

HOLD = 2.0
MANIFEST = b'{"place":"none"}'
FIXTURE = r'''
import os, signal, sys, time
from pathlib import Path
root, role, case = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if role == 'child' and case == 'long-output':
    for i in range(6000):
        print('Synthetic history %05d ' % i + 'x' * 95)
    sys.stdout.flush()
phase = 'magenta'
def paint(*_):
    color = '0;0;0' if role == 'parent' else ('0;255;0' if phase == 'green' else '255;0;255')
    state = 'blocked' if case == 'blocked-synthetic' and role == 'child' else 'idle'
    question = '\r\nSYNTHETIC question: choose blue or red' if state == 'blocked' else ''
    sys.stdout.write('\033]7501;state=' + state + ':app=fixture\033\\')
    sys.stdout.write('\033[48;2;' + color + 'm\033[2J\033[H\033[37m' + role.upper() + ' FIXTURE ' + case + question)
    sys.stdout.flush()
dirty = False
def resized(*_):
    global dirty
    dirty = True
signal.signal(signal.SIGWINCH, resized)
paint()
(root / (role + '.ready')).write_text('ready')
deadline = time.monotonic() + 180
while time.monotonic() < deadline:
    if role == 'child' and (root / 'update').exists() and phase != 'green':
        phase = 'green'
        dirty = True
    if dirty:
        dirty = False
        paint()
        if phase == 'green':
            (root / 'update.ack').write_text('green')
    time.sleep(.02)
'''


def run(args, env, timeout=35):
    p = subprocess.run([str(a) for a in args], env=env, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"{args[:3]} rc={p.returncode}: {p.stdout[-1800:]} {p.stderr[-1800:]}")
    return p.stdout.strip()


def obj(args, env, timeout=35):
    value = json.loads(run(args, env, timeout))
    if isinstance(value, dict) and 'error' in value:
        raise RuntimeError(str(value))
    return value


def pixels(path, target):
    """Count exact RGB pixels in the 8-bit, noninterlaced RGB(A) PNG app shots."""
    b = Path(path).read_bytes()
    if b[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    at, packed, header = 8, bytearray(), None
    while at < len(b):
        size = struct.unpack('>I', b[at:at+4])[0]
        kind, data = b[at+4:at+8], b[at+8:at+8+size]
        at += size + 12
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', data)
        elif kind == b'IDAT':
            packed.extend(data)
        elif kind == b'IEND':
            break
    w, h, depth, color, compression, filtering, interlace = header
    if depth != 8 or color not in (2, 6) or (compression, filtering, interlace) != (0, 0, 0):
        raise ValueError(f'unsupported PNG header {header}')
    stride = 3 if color == 2 else 4
    raw = zlib.decompress(packed)
    width = w * stride
    if len(raw) != h * (width + 1):
        raise ValueError('unexpected PNG data length')
    previous, count = bytearray(width), 0
    for y in range(h):
        pos = y * (width + 1)
        filt, row = raw[pos], bytearray(raw[pos+1:pos+1+width])
        for x in range(width):
            a = row[x-stride] if x >= stride else 0
            c = previous[x-stride] if x >= stride else 0
            v = previous[x]
            if filt == 1:
                predictor = a
            elif filt == 2:
                predictor = v
            elif filt == 3:
                predictor = (a + v) // 2
            elif filt == 4:
                p = a + v - c
                distances = (abs(p-a), abs(p-v), abs(p-c))
                predictor = (a, v, c)[distances.index(min(distances))]
            elif filt == 0:
                predictor = 0
            else:
                raise ValueError(f'unknown PNG filter {filt}')
            row[x] = (row[x] + predictor) & 255
        count += sum(tuple(row[x:x+3]) == target for x in range(0, width, stride))
        previous = row
    return {'width': w, 'height': h, 'matching_pixels': count}


def wait_file(path, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.is_file():
            return
        time.sleep(.02)
    raise TimeoutError(f'fixture did not write {path}')


class ScanHold:
    def __init__(self, home, phase):
        self.path = home / 'mods' / ('latency-' + phase)
        self.path.mkdir(parents=True)
        (self.path / 'main.js').write_text('export default null;\n')
        self.fifo = self.path / 'mod.json'
        os.mkfifo(self.fifo)
        self.acquired, self.cancel = threading.Event(), threading.Event()
        self.record = {'hold_requested_ms': HOLD * 1000}
        self.error = None
        self.thread = threading.Thread(target=self.writer, daemon=True)

    def writer(self):
        fd = None
        try:
            end = time.monotonic() + 15
            while not self.cancel.is_set() and time.monotonic() < end:
                try:
                    fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
                    break
                except OSError as e:
                    if e.errno != 6:  # ENXIO: no reader yet.
                        raise
                    self.cancel.wait(.01)
            if fd is None:
                raise TimeoutError('no FIFO scan reader within15s')
            self.record['reader_acquired_ns'] = time.monotonic_ns()
            self.acquired.set()
            self.cancel.wait(HOLD)
            # Future scans see a regular file; the current reader retains the FIFO inode.
            tmp = self.path / 'manifest.tmp'
            tmp.write_bytes(MANIFEST)
            os.replace(tmp, self.fifo)
            os.write(fd, MANIFEST)
            self.record['writer_released_ns'] = time.monotonic_ns()
        except BaseException as e:
            self.error = repr(e)
            self.acquired.set()
        finally:
            if fd is not None:
                os.close(fd)

    def __enter__(self):
        self.thread.start()
        if not self.acquired.wait(16) or self.error:
            self.__exit__(None, None, None)
            raise RuntimeError(self.error or 'FIFO reader timeout')
        return self

    def __exit__(self, *_):
        self.cancel.set()
        self.thread.join(3)
        if self.thread.is_alive():
            raise RuntimeError('FIFO writer did not stop')
        if self.error:
            raise RuntimeError(self.error)


def screenshot_until(app, env, out, color, deadline=6):
    end = time.monotonic() + deadline
    attempts = 0
    while time.monotonic() < end:
        attempts += 1
        run([app, 'shot', out], env)
        captured = time.monotonic_ns()
        match = pixels(out, color)
        if match['matching_pixels'] >= 1000:
            return dict(match, attempts=attempts, capture_completed_ns=captured, verified_ns=time.monotonic_ns(), png_sha256=hashlib.sha256(out.read_bytes()).hexdigest())
        time.sleep(.03)
    raise RuntimeError(f'expected fixture color {color} not visible; {match}')


def one(repo, binaries, out, variant, case, trial, sequence):
    name = f'lp-{os.getpid()}-{sequence}'
    root = Path('/var/tmp/herdr-app-' + name)
    caseout = out / f'{sequence:02d}-{variant}-{case}-{trial}'
    caseout.mkdir()
    # Owned unique session only; never inherit a terminal or socket from the caller.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HC_', 'HERDR_', 'PI_'))}
    runtime = root / 'runtime'
    runtime.mkdir(mode=0o700, parents=True)
    env.update(HERDR_APP_SANDBOX=name, HERDR_APP_BIN=str(binaries / ('herdr-app-' + variant)),
               XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(root / 'config'), WAYLAND_DISPLAY='',
               HERDR_APP_HOME=str(root / 'home'), XDG_STATE_HOME=str(root / 'state'),
               HC_SESSION='herdr-app-' + name, HERDR_APP_NO_SOUND='1',
               PATH=str(binaries / 'bin') + ':' + env['PATH'])
    (root / 'home').mkdir(parents=True)
    (root / 'home/shared.json').write_text('{"frame.welcomed":true,"layout":"main"}')
    fixture = caseout / 'fixture.py'
    fixture.write_text(FIXTURE)
    app = env['HERDR_APP_BIN']
    # Reuse the repository sandbox. Its normal build step is explicitly replaced
    # with a prebuilt-binary check, not a fake cargo invocation. Original stays intact.
    helper = caseout / 'sandbox-prebuilt'
    source = (repo / 'scripts/sandbox').read_text()
    build = '(cd "$here/.." && cargo build)'
    if source.count(build) != 1:
        raise ValueError('sandbox build hook changed')
    source = source.replace(build, 'test -x "$bin"')
    # Ubuntu22.04 Weston9 expects the full backend module name.
    source = source.replace('--backend=headless ', '--backend=headless-backend.so --use-pixman ')
    helper.write_text(source)
    helper.chmod(0o755)
    row = dict(variant=variant, case=case, trial=trial, sequence=sequence,
               terminal_mode='synthetic-main-screen', history_lines_emitted=6000 if case == 'long-output' else 0,
               load_start=os.getloadavg(), started_unix=time.time(), metrics={})
    child = parent = None
    try:
        run(['hc', 'server', 'start'], env, 15)
        run([helper, 'up'], env, 45)
        display = (root / 'display').read_text().strip()
        env['DISPLAY'] = display
        parent = obj([app, 'do', 'space.new', json.dumps({'name':'probe', 'folder':str(caseout), 'argv':[sys.executable, str(fixture), str(caseout), 'parent', case]})], env)
        wait_file(caseout / 'parent.ready')
        run(['hc', 'run', '--bg', '--no-notify', '--parent', parent['terminal'], '--name', 'fixture-child', '--', sys.executable, fixture, caseout, 'child', case], env)
        listing = obj(['hc', 'ls', '--json'], env)
        terms = listing.get('terminals', []) if isinstance(listing, dict) else listing
        child = next(t['address'] for t in terms if t.get('name') == 'fixture-child')
        wait_file(caseout / 'child.ready')
        def contains_address(value):
            if isinstance(value, dict):
                return any(contains_address(v) for v in value.values())
            if isinstance(value, list):
                return any(contains_address(v) for v in value)
            return value == child
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if contains_address(obj([app, 'ls'], env, 3)):
                row['child_metadata_seen_ns'] = time.monotonic_ns()
                break
            time.sleep(.02)
        else:
            raise RuntimeError('child absent from app world readback')
        run([app, 'shot', caseout / 'before.png'], env)
        before = pixels(caseout / 'before.png', (255, 0, 255))
        if before['matching_pixels'] >= 1000:
            raise RuntimeError('child marker visible before modal open: invalid control')
        row['before_marker'] = before
        row['parent_terminal'], row['child_terminal'] = parent['terminal'], child
        for phase in ('open', 'update'):
            with ScanHold(root / 'home', phase) as hold:
                def status():
                    started = time.monotonic_ns()
                    value = obj([app, 'status'], env)
                    done = time.monotonic_ns()
                    if value.get('space') != parent['space'] or value.get('errors'):
                        raise RuntimeError(f'unexpected status readback {value}')
                    return {'started_ns':started, 'done_ns':done, 'elapsed_ms':(done-started)/1e6, 'readback':value}
                def visible():
                    started = time.monotonic_ns()
                    if phase == 'open':
                        command = obj([app, 'do', 'terminal.peek', json.dumps({'terminal':child})], env)
                    else:
                        (caseout / 'update').write_text('green')
                        command = {'fixture_update_file_written':True}
                    command_done = time.monotonic_ns()
                    match = screenshot_until(app, env, caseout / (phase + '.png'), (255, 0, 255) if phase == 'open' else (0, 255, 0))
                    state = obj([app, 'status'], env)
                    if 'layout' not in state['overlays']:
                        raise RuntimeError('terminal modal absent on readback')
                    if phase == 'update':
                        wait_file(caseout / 'update.ack', 3)
                    return {'started_ns':started, 'command_done_ns':command_done, 'command_ms':(command_done-started)/1e6,
                            'screenshot_capture_upper_bound_ms':(match['capture_completed_ns']-started)/1e6, 'pixel_verification_ms':(match['verified_ns']-match['capture_completed_ns'])/1e6, 'screenshot':match, 'command':command, 'state':state}
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    a, b = pool.submit(status), pool.submit(visible)
                    observed = {'status':a.result(), 'visible':b.result()}
                # Do not end the artificial I/O delay early just because fixed is responsive.
                hold.thread.join(HOLD + 2)
                if hold.thread.is_alive() or hold.error:
                    raise RuntimeError(hold.error or 'FIFO did not release')
                for metric in observed.values():
                    if not hold.record['reader_acquired_ns'] <= metric['started_ns'] < hold.record['writer_released_ns']:
                        raise RuntimeError('measurement did not start during confirmed reader hold')
                observed['hold'] = hold.record
                row['metrics'][phase] = observed
        pid = int((root / 'pid').read_text())
        status_text = Path(f'/proc/{pid}/status').read_text()
        row['app_rss_kb'] = int(next(l.split()[1] for l in status_text.splitlines() if l.startswith('VmRSS:')))
        row['observations_complete'] = True
    except BaseException as e:
        row['error'] = repr(e)
        raise
    finally:
        row['load_end'] = os.getloadavg()
        cleanup_errors = []
        for terminal in (child, parent.get('terminal') if parent else None):
            if terminal:
                try:
                    p = subprocess.run(['hc', 'kill', terminal], env=env, capture_output=True, timeout=10)
                    if p.returncode:
                        cleanup_errors.append(f'kill {terminal} rc={p.returncode}')
                except Exception as e:
                    cleanup_errors.append(repr(e))
        for file in ('app.log', 'weston.log', 'display'):
            try:
                if (root / file).is_file():
                    shutil.copyfile(root / file, caseout / file)
            except Exception as e:
                cleanup_errors.append(repr(e))
        try:
            clean = subprocess.run([helper, 'down'], env=env, capture_output=True, text=True, timeout=20)
            row['sandbox_down_rc'] = clean.returncode
            if clean.returncode:
                cleanup_errors.append(clean.stderr)
        except Exception as e:
            cleanup_errors.append(repr(e))
        row['cleanup_errors'] = cleanup_errors
        (caseout / 'result.json').write_text(json.dumps(row, indent=2))
        with (out / 'results.jsonl').open('a') as f:
            f.write(json.dumps(row) + '\n')
        if cleanup_errors:
            raise RuntimeError(f'sandbox cleanup failed: {cleanup_errors}')
    print(json.dumps({'variant':variant, 'case':case, 'trial':trial, 'metrics':{p:{'status_ms':m['status']['elapsed_ms'], 'screenshot_ms':m['visible']['screenshot_capture_upper_bound_ms']} for p,m in row['metrics'].items()}}), flush=True)


def self_check():
    import ast
    import tempfile
    ast.parse(FIXTURE)
    def chunk(kind, b):
        return struct.pack('>I',len(b))+kind+b+struct.pack('>I',zlib.crc32(kind+b))
    with tempfile.TemporaryDirectory(prefix='latency-probe-check-') as d:
        p = Path(d) / 'pixel.png'
        p.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\xff\0\xff'))+chunk(b'IEND',b''))
        assert pixels(p,(255,0,255)) == {'width':1,'height':1,'matching_pixels':1}
        assert pixels(p,(0,255,0))['matching_pixels'] == 0
        hold = ScanHold(Path(d), 'self-check')
        received = []
        reader = threading.Thread(target=lambda: received.append(hold.fifo.read_bytes()))
        reader.start()
        with hold:
            assert hold.record['reader_acquired_ns'] > 0
        reader.join(2)
        assert not reader.is_alive() and received == [MANIFEST]
        assert hold.fifo.is_file() and hold.fifo.read_bytes() == MANIFEST
    print('self-check: fixture syntax, PNG marker decode, FIFO synchronization/release only; no app/core/UI launched')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path.cwd())
    ap.add_argument('--binaries', type=Path)
    ap.add_argument('--out', type=Path)
    ap.add_argument('--self-check', action='store_true')
    a = ap.parse_args()
    if a.self_check:
        self_check()
        return
    if sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true':
        ap.error('native probe is restricted to the admitted disposable Linux CI runner')
    if not a.binaries or not a.out:
        ap.error('--binaries and --out are required')
    a.out = a.out.resolve()
    a.out.mkdir(parents=True, exist_ok=False)
    envinfo = {'platform':sys.platform, 'uname':list(os.uname()), 'cpus':os.cpu_count(), 'python':sys.version,
               'limitations':['Synthetic fixture, not real Pi async_ask', 'Main-screen scrollback, not a model transcript or old session', 'Native screenshot RPC/encoding-inclusive upper bounds, not physical first paint; offline PNG verification duration reported separately', 'No arbitrary performance threshold; inspect numbers', 'Artificial2s filesystem stall, not an observed frequency distribution']}
    (a.out / 'environment.json').write_text(json.dumps(envinfo, indent=2))
    sequence = 0
    for trial in range(1,4):
        for case in ('plain', 'blocked-synthetic', 'long-output'):
            order = ('baseline','fixed') if trial % 2 else ('fixed','baseline')
            for variant in order:
                sequence += 1
                one(a.repo.resolve(), a.binaries.resolve(), a.out, variant, case, trial, sequence)


if __name__ == '__main__':
    main()
