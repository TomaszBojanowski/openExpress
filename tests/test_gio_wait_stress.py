"""gio_storage._wait under load, in worker threads, then the main thread.

Made-up Gio operations (gio_fake.py), drawn with a fixed seed, answer at once, later, only
once cancelled, or never. Each wait ends in time with what the operation gave, in its own
thread, leaving no source or pushed context; no window callback runs in a worker. The long
run needs OPENEXPRESS_TESTS_STRESS=1."""

import os
import random
import threading
import time
from collections import Counter

import pytest
from gio_fake import WHEN, GioOp, WindowCallbacks

gio_storage = pytest.importorskip("openexpress.device.gio_storage")
GLib = gio_storage.GLib

SEED = 2026
LIMIT = 0.02
GRACE = 0.02  # CANCEL_WAIT_S of the run
SLACK = 0.5  # for a computer under load
BOUND = LIMIT + GRACE + SLACK
WORKERS = 4
WHAT = "Cannot do it"
NOT_IN_TIME = f"{WHAT}: the device did not answer in time"
# The main thread while the workers wait: holds the global default context and turns it (the
# window between events: a Gio.Application holds it all the time), turns it by hand without
# holding it, holds it without turning it (a busy window's thread), or has let it go (the
# window's loop has ended).
MODES = ("idle", "turned", "busy", "ended")


def draw(rng: random.Random) -> GioOp:
    when = rng.choices(WHEN, weights=(10, 20, 30, 20, 20))[0]
    delay = (rng.uniform(0, LIMIT / 2) if when == "before"
             else rng.uniform(0, GRACE / 2) if when == "on cancel" else 0.0)
    return GioOp(when, says=rng.choice(("done", "error")), delay_s=delay,
                 on_cancel=rng.choice(("done", "cancelled", "error")))


class Recording:
    """gio_storage's GLib: notes the sources each thread makes, and what it adds to the global
    default context."""

    def __init__(self, real):
        self._real = real
        self._lock = threading.Lock()
        self.made: dict[int, list] = {}
        self.on_default: dict[int, list] = {}

    def _note(self, table, value):
        with self._lock:
            table.setdefault(threading.get_ident(), []).append(value)
        return value

    def __getattr__(self, name):
        value = getattr(self._real, name)
        if name in ("timeout_source_new", "timeout_source_new_seconds", "idle_source_new"):
            return lambda *a: self._note(self.made, value(*a))
        if name in ("timeout_add", "timeout_add_seconds", "idle_add"):
            return lambda *a, **kw: self._note(self.on_default, value(*a, **kw))
        return value

    def take(self):
        with self._lock:
            ident = threading.get_ident()
            return self.made.pop(ident, []), self.on_default.pop(ident, [])


def one_wait(op: GioOp, recording: Recording, timed_out) -> list[tuple[str, str]]:
    """Wait for `op` as gio_storage does; what was wrong with it: (kind, details)."""
    started = time.monotonic()
    try:
        gio_storage._wait(op.start, LIMIT, WHAT)
        outcome = None
    except Exception as e:  # noqa: BLE001 - checked below
        outcome = e
    took = time.monotonic() - started
    wrong = []
    if took > BOUND:
        wrong.append(("too long", f"took {took:.3f} s"))
    gave = op.delivered[0] if op.delivered else None
    if gave == "done":
        if outcome is not None:
            wrong.append(("wrong result", f"done, but {type(outcome).__name__}: {outcome}"))
    elif gave == "error":
        if not (isinstance(outcome, gio_storage.DeviceError)
                and str(outcome) == f"{WHAT}: made-up refusal"):
            wrong.append(("wrong result", f"an error, but {outcome!r}"))
    elif not (isinstance(outcome, timed_out) and str(outcome) == NOT_IN_TIME):
        wrong.append(("wrong result", f"{gave or 'no answer'}, but {outcome!r}"))
    me = threading.current_thread().name
    if op.delivered and op.delivered[1] != me:
        wrong.append(("answer in another thread", f"{op.delivered[1]}, not {me}"))
    if GLib.MainContext.get_thread_default() is not None:
        wrong.append(("context left pushed", ""))
    made, on_default = recording.take()
    if any(not source.is_destroyed() for source in made):
        wrong.append(("source of the wait left", ""))
    if on_default:  # never looked up by ID: another thread may have destroyed it meanwhile
        wrong.append(("source added to the default context", f"{len(on_default)}"))
    return [(kind, f"{op.when}/{op.says}/{op.on_cancel}: {kind} {said}".rstrip())
            for kind, said in wrong]


def turn_default_until(threads, deadline):
    context = GLib.MainContext.default()
    while any(t.is_alive() for t in threads) and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.002)


def run_load(seed: int, per_worker: int, in_main: int, monkeypatch) -> dict:
    rng = random.Random(seed)
    recording = Recording(gio_storage.GLib)
    monkeypatch.setattr(gio_storage, "GLib", recording)
    monkeypatch.setattr(gio_storage, "CANCEL_WAIT_S", GRACE, raising=False)
    timed_out = getattr(gio_storage, "GioTimedOut", gio_storage.DeviceError)
    wrong: list[tuple[str, str, str]] = []  # (mode, kind, details)
    ops: list[GioOp] = []
    counts = {"waits": 0}
    for mode in MODES:
        plans = [[draw(rng) for _ in range(per_worker)] for _ in range(WORKERS)]
        ops += [op for plan in plans for op in plan]
        window = WindowCallbacks()
        window.add([0 if n % 4 == 0 else rng.randint(1, 200) for n in range(20)])
        workers = []

        def worker(plan, n, mode=mode):
            for number, op in enumerate(plan):
                wrong.extend((mode, kind, f"{mode}, worker {n}, wait {number}: {said}")
                             for kind, said in one_wait(op, recording, timed_out))

        for n, plan in enumerate(plans):
            workers.append(threading.Thread(target=worker, args=(plan, n),
                                            name=f"stress-worker-{n}", daemon=True))
        deadline = time.monotonic() + per_worker * BOUND + 5
        context = GLib.MainContext.default()
        held = mode in ("idle", "busy") and context.acquire()
        try:
            for w in workers:
                w.start()
            if mode in ("idle", "turned"):
                turn_default_until(workers, deadline)
            else:
                for w in workers:
                    w.join(max(0.0, deadline - time.monotonic()))
            hanging = [w.name for w in workers if w.is_alive()]
            if hanging:
                wrong.append((mode, "still waiting at the deadline", ", ".join(hanging)))
        finally:
            if held:
                context.release()
            # A worker that wrongly waits on the global default context, for an answer only
            # this thread can give it, ends here: the run reports it instead of hanging.
            turn_default_until(workers, time.monotonic() + 10)
            in_workers = sorted({name for name in window.ran
                                 if name.startswith("stress-worker")})
            window.remove_left()
        if in_workers:
            wrong.append((mode, "callbacks of the window in a worker", ", ".join(in_workers)))
        counts["waits"] += WORKERS * per_worker
        left = [w.name for w in workers if w.is_alive()]
        if left:  # they may hold the global default context: the run cannot go on
            wrong.append((mode, "threads left after the cleanup", ", ".join(left)))
            counts["wrong"] = wrong
            return counts
    for number in range(in_main):  # the command line: the main thread waits itself
        op = draw(rng)
        ops.append(op)
        wrong.extend(("main thread", kind, f"main thread, wait {number}: {said}")
                     for kind, said in one_wait(op, recording, timed_out))
        counts["waits"] += 1
    for op in ops:
        op.clean()
    counts["wrong"] = wrong
    return counts


def _check(result, seed):
    wrong = result["wrong"]
    if wrong:
        kinds = Counter((mode, kind) for mode, kind, _said in wrong)
        summary = "\n".join(f"  {mode}: {kind} ×{n}" for (mode, kind), n in kinds.items())
        first = "\n".join(f"  {said}" for _mode, _kind, said in wrong[:10])
        pytest.fail(f"seed {seed}: {len(wrong)} wrong in {result['waits']} waits\n{summary}\n"
                    f"first:\n{first}", pytrace=False)


def test_waits_under_load(monkeypatch):
    result = run_load(SEED, per_worker=10, in_main=20, monkeypatch=monkeypatch)
    _check(result, SEED)
    assert result["waits"] == 180


@pytest.mark.stress
def test_waits_under_long_load(monkeypatch):
    seed = int(os.environ.get("OPENEXPRESS_STRESS_SEED", SEED))
    result = run_load(seed, per_worker=250, in_main=250, monkeypatch=monkeypatch)
    _check(result, seed)
    assert result["waits"] == 4250
