#!/usr/bin/env python3
"""Stress test of the waits for gvfs on a real device. It ONLY READS. Not part of a release;
run by hand from the folder of the project:

    python3 tools/stress_device.py [--cycles N] [--short-timeout S] [--path cli|window|both]
                                   [--device-uri URI] [--settle S]

Each cycle:
  1. the device to ourselves for PTP, as for an app install, without its writes: unmounted
     from gvfs, a session that only reads, mounted again;
  2. unmounted and mounted again as when it is opened from its tile, then its
     GarminDevice.xml looked at;
  3. after --settle seconds: mounted, no thread left, no context left pushed, the answers
     of Gio taken by the thread that waited.
--path cli runs the cycles in the main thread (the command line), window in a worker thread
while the main thread runs a main loop (the window), both one and the other in turn.
--short-timeout gives the unmounts that time limit, to see what gvfs does with an unmount
cancelled at its time limit.

It never writes to the device or to the account: its PTP session allows only reading calls,
its storage refuses every write, and the network is closed to it. It refuses to start while a
window or the background service of openExpress runs, or any other openexpress process, and
gives a device other than the Edge 1050 at most 5 cycles (the fēnix 8 has crashed after long
work), the Edge 1050 at most 200. Exit code: 0 no errors, 1 errors in the cycles, 2 refused."""

from __future__ import annotations

import argparse
import contextlib
import itertools
import logging
import os
import socket
import statistics
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openexpress import applog  # noqa: E402
from openexpress.device import gio_storage  # noqa: E402
from openexpress.device.storage import DeviceError, RemountFailed  # noqa: E402
from openexpress.device.xmldevice import parse  # noqa: E402
from openexpress.paths import APP_ID  # noqa: E402

Gio, GLib = gio_storage.Gio, gio_storage.GLib

EDGE_1050 = "006-B4440-00"
FENIX_8 = "006-B4532-00"
MOST_ON_THE_EDGE = 200
MOST_ELSEWHERE = 5
WARNING_PAUSE_S = 5
SHOWN_S = 5.0  # how long the volume monitor may take to show a mount or an unmount
XML = "GARMIN/GarminDevice.xml"
PROC = Path("/proc")
NAMES = ((APP_ID, "the window of openExpress"),
         (APP_ID + ".Watch", "the background service of openExpress"))
READING = frozenset({"storages", "free_space", "info", "children", "lookup"})
WRITES = ("_write", "_write_file", "_delete", "_mkdir")
STEPS = ("unmount", "PTP session open", "PTP reads", "mount again", "mount", "open", "cycle")


class Refused(Exception):
    """The script does not start; said, exit code 2."""


class Stop(Exception):
    """The run cannot go on (the device is left without a mount)."""


class ReadOnlyRefused(RuntimeError):
    """A call that would change the device: a mistake of this script, never made."""


# --- what it only reads -----------------------------------------------------------------------


class ReadOnlySession:
    """A PTP session of which only the reading calls are there."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        if name not in READING:
            raise ReadOnlyRefused(f"the stress test only reads: {name} refused")
        return getattr(self._session, name)


def guard_storage(storage):
    """`storage`, whose every write to the device is refused."""
    def refused(*_a, **_kw):
        raise ReadOnlyRefused("the stress test only reads: a write to the device refused")

    for name in WRITES:
        setattr(storage, name, refused)
    return storage


@contextlib.contextmanager
def network_closed():
    """No connection beyond Unix sockets (D-Bus and gvfs use them, from C)."""
    saved = (socket.getaddrinfo, socket.create_connection, socket.socket.connect,
             socket.socket.connect_ex)
    lookup, create, connect, connect_ex = saved

    def refused(*_a, **_kw):
        raise OSError("the stress test does not reach the network")

    def through(real):
        def call(sock, address):
            if sock.family != socket.AF_UNIX:
                refused()
            return real(sock, address)
        return call

    socket.getaddrinfo, socket.create_connection = refused, refused
    socket.socket.connect, socket.socket.connect_ex = through(connect), through(connect_ex)
    try:
        yield
    finally:
        (socket.getaddrinfo, socket.create_connection, socket.socket.connect,
         socket.socket.connect_ex) = lookup, create, connect, connect_ex


# --- whether it may start ---------------------------------------------------------------------


def _bus_has_owner(name: str) -> bool:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (name,)),
                          GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 3000, None)
    return bool(reply.unpack()[0])


def _ancestors(pid: int) -> set[int]:
    """This process and those that started it."""
    mine = set()
    while pid > 1 and pid not in mine:
        mine.add(pid)
        try:
            stat = (PROC / str(pid) / "stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return mine


def _is_openexpress(argv: list[str]) -> bool:
    if any(os.path.basename(a) == "openexpress" for a in argv):
        return True  # the entry point: /usr/bin/openexpress, /app/bin/openexpress
    return any(a == "-m" and b.split(".")[0] == "openexpress"
               for a, b in itertools.pairwise(argv))


def others_running() -> list[str]:
    """What of openExpress runs besides this script."""
    said = []
    for name, what in NAMES:
        try:
            if _bus_has_owner(name):
                said.append(what)
        except Exception as e:  # noqa: BLE001 - not known: not started, to be sure
            said.append(f"the session bus could not be asked ({type(e).__name__})")
    mine = _ancestors(os.getpid())
    for entry in sorted(PROC.iterdir(), key=lambda p: p.name):
        if not entry.name.isdigit() or int(entry.name) in mine:
            continue
        try:
            argv = [a.decode(errors="replace")
                    for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        if _is_openexpress(argv):
            said.append(f"an openexpress process (PID {entry.name})")
    return said


def cycle_limit(part_number: str) -> int:
    return MOST_ON_THE_EDGE if part_number == EDGE_1050 else MOST_ELSEWHERE


# --- what it says -----------------------------------------------------------------------------


@dataclass
class Summary:
    device: str = ""
    asked: int = 0
    done: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    notes: Counter = field(default_factory=Counter)
    limits: Counter = field(default_factory=Counter)
    times: dict[str, list[float]] = field(default_factory=dict)
    last: dict[str, float] = field(default_factory=dict)

    def time(self, step: str, seconds: float) -> None:
        self.times.setdefault(step, []).append(seconds)
        self.last[step] = seconds

    def error(self, kind: str, text: str = "") -> None:
        self.errors.append((kind, applog.redact(text)))

    def lines(self) -> list[str]:
        said = [f"Device: {self.device}", f"Cycles: {self.done} of {self.asked}",
                f"Errors: {len(self.errors)}"]
        said += [f"  {kind}: {n}" for kind, n in Counter(k for k, _t in self.errors).items()]
        said += [f"    {kind}: {text}" for kind, text in self.errors[:10] if text]
        if self.limits:
            said.append("Time limits reached:")
            said += [f"  {what}: {n}" for what, n in self.limits.items()]
        if self.notes:
            said.append("Notes:")
            said += [f"  {what}: {n}" for what, n in self.notes.items()]
        said.append("Times (median / maximum):")
        said += [f"  {step}: {statistics.median(v):.2f} s / {max(v):.2f} s ({len(v)}×)"
                 for step in STEPS if (v := self.times.get(step))]
        return said


SETTLED = {"mounted": "PTP: the device settled mounted",
           "unmounted": "PTP: the device settled unmounted",
           "between": "PTP: the device settled mounted without its files",
           "not settled": "PTP: the device did not settle"}


class Limits(logging.Handler):
    """What gio_storage said when an operation reached its time limit: the answer that came
    after the cancel, and after an unmount for PTP that did not finish in time how the device
    settled, whether it was mounted again and whether it came back."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.seen: Counter = Counter()

    def emit(self, record):
        said = record.getMessage()
        if said.startswith("gio: "):
            self._gio(said)
        elif said.startswith("PTP: the device is ") and " (after " in said:
            state = said.removeprefix("PTP: the device is ").split(" (after ", 1)[0]
            self.seen[SETTLED.get(state, f"PTP: the device is {state}")] += 1
        elif said.startswith("PTP: mounting the device again (try "):
            tried = said.removeprefix("PTP: mounting the device again (try ").split(" ", 1)[0]
            self.seen[f"PTP: mounted again (try {tried})"] += 1
        elif said == "PTP: the device did not come back":
            self.seen["PTP: the device did not come back"] += 1

    def _gio(self, said):
        name = said.split(":", 2)[1].strip()
        if "ended all the same" in said:
            self.seen[f"{name}: ended all the same after the cancel"] += 1
        elif "given up" in said:
            self.seen[f"{name}: no answer even after the cancel"] += 1
        elif "cancelled" in said:
            answer = said.split("its answer: ", 1)[1].split(" (", 1)[0] \
                if "its answer: " in said else "?"
            self.seen[f"{name}: cancelled, its answer: {answer}"] += 1


@contextlib.contextmanager
def instrumented(summary: Summary, answers: list, short_timeout: float | None):
    """gio_storage timed, its answers noted with the thread that took them, and its unmounts
    given the short time limit, for the run only."""
    names = ("_unmount", "_remount", "mount", "_wait", "UNMOUNT_TIMEOUT_S")
    saved = {name: getattr(gio_storage, name) for name in names}

    def timed(name, step):
        real = saved[name]

        def call(*a, **kw):
            started = time.monotonic()
            try:
                return real(*a, **kw)
            finally:
                summary.time(step, time.monotonic() - started)
        return call

    def noting_wait(start, timeout_s, what, name="operation"):
        waiting = threading.current_thread().name

        def begin(done, cancellable):
            def noted(finish):
                answers.append((name, waiting, threading.current_thread().name))
                done(finish)
            start(noted, cancellable)
        return saved["_wait"](begin, timeout_s, what, name)

    gio_storage._unmount = timed("_unmount", "unmount")
    gio_storage._remount = timed("_remount", "mount again")
    gio_storage.mount = timed("mount", "mount")
    gio_storage._wait = noting_wait
    if short_timeout is not None:
        gio_storage.UNMOUNT_TIMEOUT_S = short_timeout
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(gio_storage, name, value)


# --- a cycle ----------------------------------------------------------------------------------


def _entry(uri: str):
    key = gio_storage._key(uri)
    return next((d for d in gio_storage.find_devices() if gio_storage._key(d.uri) == key), None)


def _pump() -> None:
    """In the main thread outside a main loop (the command line): the volume monitor gets its
    signals only while the global default context turns."""
    if threading.current_thread() is threading.main_thread():
        context = GLib.MainContext.default()
        for _turn in range(100):
            if not context.iteration(False):
                break


def wait_for(found, limit_s: float):
    """`found()`, once it gives something within `limit_s`; None otherwise."""
    deadline = time.monotonic() + limit_s
    while True:
        _pump()
        value = found()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(0.05)


def pause(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _pump()
        time.sleep(0.05)


def ptp_part(storage, summary: Summary, short: bool) -> None:
    opener = storage.exclusive_ptp()
    started = time.monotonic()
    summary.last.pop("unmount", None)
    try:
        with opener as raw:
            entered = time.monotonic()
            summary.time("PTP session open",
                         max(0.0, entered - started - summary.last.get("unmount", 0.0)))
            session = ReadOnlySession(raw)
            store = next((s for s in session.storages()
                          if session.lookup(s, "GARMIN") is not None), None)
            item = session.lookup(store, XML) if store is not None else None
            if item is None or item.is_dir or not item.size:
                summary.error("PTP: GarminDevice.xml not found", "")
            summary.time("PTP reads", time.monotonic() - entered)
    except ReadOnlyRefused:
        raise
    except gio_storage.GioTimedOut as e:
        if short:
            summary.limits["PTP: the unmount stopped at its limit"] += 1
        else:
            summary.error("PTP: the unmount timed out", str(e))
    except RemountFailed as e:
        summary.error("PTP: not mounted again", str(e))
    except Exception as e:  # noqa: BLE001 - every error of the device is counted
        summary.error(f"PTP: {type(e).__name__}", str(e))


def open_part(uri: str, summary: Summary, short: bool) -> None:
    try:
        gio_storage._unmount(uri)
    except gio_storage.GioTimedOut as e:
        if short:
            summary.limits["unmount stopped at its limit"] += 1
        else:
            summary.error("unmount timed out", str(e))
    except DeviceError as e:
        summary.error("unmount", str(e))
        return
    # Shown not mounted (after an unmount stopped at its limit it may stay mounted).
    entry = wait_for(lambda: (e := _entry(uri)) is not None and not e.mounted and e,
                     SHOWN_S if not short else 2.0) or _entry(uri)
    if entry is None:
        summary.error("the device is not listed after the unmount", "")
        return
    try:
        gio_storage.mount(entry)
    except DeviceError as e:
        summary.error("mount", str(e))
        return
    started = time.monotonic()
    try:
        storage = guard_storage(wait_for(lambda: _openable(uri), SHOWN_S) or
                                gio_storage.open_device_storage(uri))
        if not storage.exists(XML) or storage.free_space() is None:
            summary.error("open: GarminDevice.xml or the free space not read", "")
    except DeviceError as e:
        summary.error("open", str(e))
    summary.time("open", time.monotonic() - started)


def _openable(uri: str):
    try:
        return gio_storage.open_device_storage(uri)
    except DeviceError:
        return None


def check_part(uri: str, summary: Summary, settle_s: float, baseline: int,
               answers: list) -> None:
    pause(settle_s)
    entry = wait_for(lambda: (e := _entry(uri)) is not None and e.mounted and e, SHOWN_S)
    if not entry:
        summary.error("left without a mount", "")
        left = _entry(uri)
        if left is None:
            raise Stop("the device is gone: unplug it and plug it in again")
        try:
            gio_storage.mount(left)
        except DeviceError as e:
            raise Stop(f"the device could not be mounted again ({applog.redact(str(e))}): "
                       "unplug it and plug it in again") from e
    else:
        storage = _openable(uri)
        if storage is None or not guard_storage(storage).exists(XML):
            summary.error("mounted, but GarminDevice.xml not visible", "")
    if threading.active_count() != baseline:
        summary.error("threads left", f"{threading.active_count() - baseline}")
    if (threading.current_thread() is not threading.main_thread()
            and GLib.MainContext.get_thread_default() is not None):
        summary.error("a context left pushed", "")
    if gio_storage._quiet_until.get(gio_storage._key(uri)) == float("inf"):
        summary.error("the quiet time of the device left for ever", "")
    for name, waiting, answered in answers:
        if waiting != answered:
            summary.notes[f"{name}: answer taken by {answered}, not by {waiting}"] += 1


def run_cycle(uri, storage, summary, args, answers, stop) -> None:
    baseline = threading.active_count()
    first = len(answers)
    started = time.monotonic()
    short = args.short_timeout is not None
    ptp_part(storage, summary, short)
    if not stop.is_set():
        open_part(uri, summary, short)
    if not stop.is_set():
        check_part(uri, summary, args.settle, baseline, answers[first:])
    summary.time("cycle", time.monotonic() - started)


def in_a_worker(cycle, stop) -> None:
    """The cycle in a worker thread while the main thread runs a main loop, as the window's."""
    said = {}

    def work():
        try:
            cycle()
        except BaseException as e:  # noqa: BLE001 - passed on in the main thread
            said["error"] = e

    worker = threading.Thread(target=work, name="stress-cycle")
    loop = GLib.MainLoop()

    def check():
        if worker.is_alive():
            return True
        loop.quit()
        return False

    worker.start()
    GLib.timeout_add(100, check)
    try:
        loop.run()
    except KeyboardInterrupt:
        stop.set()  # the worker ends its step (its waits have time limits)
        while worker.is_alive():
            GLib.MainContext.default().iteration(False)
            time.sleep(0.02)
        raise
    if "error" in said:
        raise said["error"]


# --- the run ----------------------------------------------------------------------------------


def _arguments(argv):
    p = argparse.ArgumentParser(description="Stress test of the waits for gvfs on a real "
                                            "device. It only reads.")
    p.add_argument("--cycles", type=int, default=5)
    p.add_argument("--short-timeout", type=float, default=None, metavar="S",
                   help="time limit of the unmounts, in seconds (e.g. 0.05)")
    p.add_argument("--path", choices=("cli", "window", "both"), default="both")
    p.add_argument("--device-uri", default=None)
    p.add_argument("--settle", type=float, default=3.0, metavar="S")
    args = p.parse_args(argv)
    if args.cycles < 1 or (args.short_timeout is not None and args.short_timeout <= 0) \
            or args.settle < 0:
        p.error("--cycles must be 1 or more, --short-timeout and --settle above 0")
    return args


def _device(args):
    devices = gio_storage.find_devices()
    if args.device_uri:
        devices = [d for d in devices
                   if gio_storage._key(d.uri) == gio_storage._key(args.device_uri)]
    if not devices:
        raise Refused("no MTP device found" + (" at that address" if args.device_uri else ""))
    if len(devices) > 1:
        raise Refused(f"{len(devices)} MTP devices connected: connect only one, or choose it "
                      "with --device-uri")
    found = devices[0]
    if not found.mounted:
        raise Refused("the device is not mounted: open it in Files first")
    storage = guard_storage(gio_storage.open_device_storage(found.uri))
    device = parse(storage.read(XML))
    limit = cycle_limit(device.part_number)
    if args.cycles > limit:
        raise Refused(f"{device.description} ({device.part_number}): at most {limit} cycles")
    if storage.exclusive_ptp() is None:
        raise Refused("no PTP session of our own is possible here (the Flatpak, or no libusb)")
    return found.uri, storage, device


def run(args) -> int:
    others = others_running()
    if others:
        raise Refused("openExpress is running (" + "; ".join(others) + "). Close it first.")
    with network_closed():
        applog.setup("stress", stderr_level=logging.ERROR)
        try:
            uri, storage, device = _device(args)
            return _cycles(args, uri, storage, device)
        finally:
            applog.close()


def _cycles(args, uri, storage, device) -> int:
    if device.part_number == FENIX_8:
        print("Warning: the fēnix 8 has crashed (RTL Trap) after long work. This "
              f"test only reads, at most {MOST_ELSEWHERE} cycles. Ctrl+C within "
              f"{WARNING_PAUSE_S} s stops it.", flush=True)
        try:
            time.sleep(WARNING_PAUSE_S)
        except KeyboardInterrupt:
            print("Stopped before the first cycle; nothing was done.")
            return 0
    summary = Summary(device=f"{device.description} ({device.part_number})", asked=args.cycles)
    print(f"Device: {summary.device}; {args.cycles} cycle(s), path {args.path}"
          + (f", unmounts limited to {args.short_timeout:g} s" if args.short_timeout else ""),
          flush=True)
    answers: list = []
    limits = Limits()
    gio_log = logging.getLogger(gio_storage.__name__)
    gio_log.addHandler(limits)
    stop = threading.Event()
    try:
        with instrumented(summary, answers, args.short_timeout):
            for number in range(args.cycles):
                path = args.path if args.path != "both" else ("cli", "window")[number % 2]
                print(f"Cycle {number + 1}/{args.cycles} ({path})", flush=True)

                def cycle():
                    run_cycle(uri, storage, summary, args, answers, stop)

                if path == "cli":
                    cycle()
                else:
                    in_a_worker(cycle, stop)
                summary.done += 1
    except KeyboardInterrupt:
        stop.set()
        print("Interrupted: what was done so far.")
    except Stop as e:
        summary.error("stopped", str(e))
        print(f"Stopped: {e}")
    finally:
        gio_log.removeHandler(limits)
    summary.limits.update(limits.seen)
    for line in summary.lines():
        print(line)
    return 1 if summary.errors else 0


def main(argv=None) -> int:
    args = _arguments(argv)
    try:
        return run(args)
    except Refused as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
