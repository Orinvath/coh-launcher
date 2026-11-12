"""Receive-rate telemetry for one update or install process.

Only the job's own process and the children found from /proc ancestry are
counted. Every sample reads the per-socket counter `bytes_received` from the
installed `ss` tool, with fixed arguments. Nothing is captured or stored. Only
the total and a rolling rate leave this module.

Counting rules:
  * a socket seen for the first time counts its current bytes_received,
    but only when its owner started at or after the job's root process;
  * later samples add positive deltas only, so a counter reset adds nothing;
  * sockets that close keep their counted total;
  * a process that is no longer alive, or whose start time changed, is dropped
    so a recycled PID cannot be counted.
"""

import collections
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

SS_PATH = "/usr/bin/ss"
SS_ARGS = ["-tinHpeO"]
SS_TIMEOUT_MS = 1000
SAMPLE_MS = 1000
RATE_SAMPLES = 3
PROC = Path("/proc")
PID_RE = re.compile(r"pid=(\d+)")
SK_RE = re.compile(r"\bsk:([0-9a-fA-F]+)")
RECV_RE = re.compile(r"\bbytes_received:(\d+)")


def parse_ss(text):
    """Return [(socket_cookie, owner_pids, bytes_received)] from `ss -tinHpeO` output."""
    rows = []
    for line in (text or "").splitlines():
        cookie = SK_RE.search(line)
        received = RECV_RE.search(line)
        if cookie is None or received is None:
            continue
        pids = {int(pid) for pid in PID_RE.findall(line)}
        rows.append((cookie.group(1), pids, int(received.group(1))))
    return rows


def read_stat(pid):
    """Return (ppid, starttime_ticks) from /proc/<pid>/stat, or None when the process is gone."""
    try:
        text = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return None
    fields = text[text.rindex(")") + 2:].split()
    try:
        return int(fields[1]), int(fields[19])
    except (IndexError, ValueError):
        return None


def scan_processes():
    """Return {pid: (ppid, starttime)} for every readable process."""
    found = {}
    for entry in PROC.iterdir():
        if entry.name.isdigit():
            stat = read_stat(int(entry.name))
            if stat is not None:
                found[int(entry.name)] = stat
    return found


class ProcessTree:
    """The root process and its descendants, kept by PID and start time."""

    def __init__(self, root_pid):
        self.root_pid = root_pid
        self.root_start = None
        self.members = {}

    def refresh(self, processes):
        """Update membership from a /proc scan. Return {pid: starttime} for live members."""
        root = processes.get(self.root_pid)
        if root is None:
            self.members = {}
            return {}
        if self.root_start is None:
            self.root_start = root[1]
        live = {pid: start for pid, start in self.members.items() if processes.get(pid, (None, None))[1] == start}
        live[self.root_pid] = root[1]
        changed = True
        while changed:
            changed = False
            for pid, (ppid, start) in processes.items():
                if pid not in live and ppid in live and start >= self.root_start:
                    live[pid] = start
                    changed = True
        self.members = live
        return dict(live)


class TransferCounter:
    """Monotonic total of bytes received by the attributed sockets."""

    def __init__(self):
        self.total = 0
        self._last = {}

    def observe(self, rows, members):
        for cookie, pids, received in rows:
            if not (pids & set(members)):
                continue
            if cookie in self._last:
                if received >= self._last[cookie]:
                    self.total += received - self._last[cookie]
            else:
                self.total += received
            self._last[cookie] = received
        return self.total


class RateWindow:
    """Rate from the last RATE_SAMPLES intervals: delta bytes over actual elapsed seconds."""

    def __init__(self):
        self._points = collections.deque(maxlen=RATE_SAMPLES + 1)

    def add(self, when, total):
        self._points.append((when, total))

    def rate(self):
        if len(self._points) < 2:
            return None
        (t0, v0), (t1, v1) = self._points[0], self._points[-1]
        if t1 <= t0:
            return None
        return (v1 - v0) / (t1 - t0)


def _unavailable():
    return {"available": False, "receivedBytes": None, "bytesPerSecond": None}


class TransferSampler(QObject):
    """Samples one running process once per second. Samples never overlap; the ss call is bounded."""

    sample = Signal(object)

    def __init__(self, root_pid, parent=None):
        super().__init__(parent)
        self._tree = ProcessTree(root_pid)
        self._counter = TransferCounter()
        self._rates = RateWindow()
        self._busy = False
        self._stopped = False
        self._members = {}
        self._proc = None
        self._timer = QTimer(self)
        self._timer.setInterval(SAMPLE_MS)
        self._timer.timeout.connect(self._tick)
        self._guard = QTimer(self)
        self._guard.setSingleShot(True)
        self._guard.timeout.connect(self._ss_timed_out)

    def start(self):
        self._timer.start()

    def stop(self):
        self._stopped = True
        self._timer.stop()
        self._guard.stop()
        if self._proc is not None:
            self._proc.kill()

    def _tick(self):
        if self._busy or self._stopped:
            return
        if not os.path.exists(SS_PATH):
            self.sample.emit(_unavailable())
            return
        try:
            members = self._tree.refresh(scan_processes())
        except OSError:
            self.sample.emit(_unavailable())
            return
        if not members:
            self.sample.emit(_unavailable())
            return
        self._busy = True
        self._members = members
        self._proc = QProcess(self)
        self._proc.setProgram(SS_PATH)
        self._proc.setArguments(SS_ARGS)
        self._proc.finished.connect(self._ss_done)
        self._proc.start()
        self._guard.start(SS_TIMEOUT_MS)

    def _ss_timed_out(self):
        if self._proc is not None and self._busy:
            self._proc.kill()

    def _ss_done(self, exit_code, _status):
        self._guard.stop()
        proc, self._proc = self._proc, None
        self._busy = False
        if proc is None:
            return
        text = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        proc.deleteLater()
        if self._stopped:
            return
        if exit_code != 0 or "users:" not in text:
            self.sample.emit(_unavailable())
            return
        rows = parse_ss(text)
        owned = [row for row in rows if row[1] & set(self._members)]
        # The counter always sees the rows, so its internal total stays correct for later samples.
        total = self._counter.observe(rows, self._members)
        if not owned:
            self.sample.emit(_unavailable())
            return
        self._rates.add(time.monotonic(), total)
        self.sample.emit({"available": True, "receivedBytes": total, "bytesPerSecond": self._rates.rate()})
