"""One asynchronous process lifecycle with bounded, redacted logs and file-backed capture.

The official direct executable writes stdout and stderr to a private,
project-local capture file (mode 600), not to an OS pipe. A poll timer reads
new bytes for progress. A job finishes when the direct process exits, after a
final drain of the capture file. Timed-out or detached processes are never
killed: they are kept alive and reported as uncertain.

Runner contract, shared with the test fixtures:
    start(job_id, argv, cwd, env, timeout_ms, inactivity, label)
    signals: output(job_id: str, text: str)
             finished(job_id: str, result: dict{exit_code, timed_out, output})
             telemetry(job_id: str, dict{available, receivedBytes, bytesPerSecond})
    detach(job_id)
"""

import os
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QIODevice, QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .config import PROJECT_ROOT
from .homecoming import CLI_COMMANDS, redact
from .telemetry import TransferSampler

LOG_JOB_LIMIT = 256 * 1024
LOG_TOTAL_LIMIT = 2 * 1024 * 1024
DEFAULT_LOG_DIR = PROJECT_ROOT / "var" / "logs"
DEFAULT_CAPTURE_DIR = PROJECT_ROOT / "var" / "captures"
OUTPUT_TAIL_LIMIT = 256 * 1024
CAPTURE_ROTATE_BYTES = 512 * 1024
POLL_MS = 200
TELEMETRY_LABELS = ("update", "install")


class JobLog:
    """Writes one job's redacted output to var/logs, stopping at the per-job limit."""

    def __init__(self, directory, label):
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"{stamp}-{label}-{os.getpid()}-{time.monotonic_ns() % 100000}.log"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self._handle = os.fdopen(fd, "a", encoding="utf-8")
        self._written = 0

    def write(self, text):
        if self._handle is None or self._written >= LOG_JOB_LIMIT:
            return
        clean = redact(text)[: LOG_JOB_LIMIT - self._written]
        self._handle.write(clean)
        self._handle.flush()
        self._written += len(clean)
        if self._written >= LOG_JOB_LIMIT:
            self._handle.write("\n[log truncated at 256 KiB]\n")
            self._handle.flush()

    def close(self):
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def prune_logs(directory, limit=LOG_TOTAL_LIMIT):
    """Delete the oldest logs until the folder is within the aggregate limit."""
    directory = Path(directory)
    if not directory.is_dir():
        return
    files = sorted(directory.glob("*.log"), key=lambda item: item.stat().st_mtime)
    total = sum(item.stat().st_size for item in files)
    for item in files:
        if total <= limit:
            break
        total -= item.stat().st_size
        item.unlink()


def _new_capture(directory):
    """Create one private capture file. mkstemp makes it mode 600 with a unique name."""
    directory.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="capture-", suffix=".txt", dir=str(directory))
    os.close(fd)
    return Path(path)


def _clear_stale_captures(directory):
    """Remove capture files left by an earlier run that stopped mid-job."""
    if directory.is_dir():
        for item in directory.glob("capture-*.txt"):
            item.unlink()


class _Job:
    def __init__(self, job_id, process, log, capture, label):
        self.id = job_id
        self.process = process
        self.label = label
        self.log = log
        self.capture = capture
        self.sampler = None
        self.offset = 0
        self.text = ""
        self.exited = False
        self.exit_code = None
        self.done = False
        self.timed_out = False
        self.timer = None
        self.poll = None
        self.window_ms = 0
        self.inactivity = False


class ProcessRunner(QObject):
    output = Signal(str, str)
    timed_out = Signal(str)  # the window passed; the process is still running and still watched
    finished = Signal(str, object)
    telemetry = Signal(str, object)  # update and install only; stops when the job finishes or detaches

    def __init__(self, parent=None, log_dir=DEFAULT_LOG_DIR, capture_dir=DEFAULT_CAPTURE_DIR):
        super().__init__(parent)
        self._jobs = {}
        self._detached = []
        self._log_dir = Path(log_dir)
        self._capture_dir = Path(capture_dir)
        prune_logs(self._log_dir)
        _clear_stale_captures(self._capture_dir)

    def detached_count(self):
        return len(self._detached)

    def start(self, job_id, argv, cwd, env, timeout_ms, inactivity=False, label="job"):
        process = QProcess(self)
        process.setProgram(argv[0])
        process.setArguments(list(argv[1:]))
        process.setWorkingDirectory(cwd)
        # Start empty and copy only the given environment, so removed keys stay removed.
        environment = QProcessEnvironment()
        for key, value in env.items():
            environment.insert(key, value)
        process.setProcessEnvironment(environment)
        capture = _new_capture(self._capture_dir)
        # Both streams go to the same private file in append mode, so rotation can truncate safely.
        process.setStandardOutputFile(str(capture), QIODevice.OpenModeFlag.Append)
        process.setStandardErrorFile(str(capture), QIODevice.OpenModeFlag.Append)
        job = _Job(job_id, process, JobLog(self._log_dir, label), capture, label)
        job.window_ms = timeout_ms
        job.inactivity = inactivity
        job.timer = QTimer(self)
        job.timer.setSingleShot(True)
        job.timer.timeout.connect(lambda: self._timed_out(job))
        job.poll = QTimer(self)
        job.poll.setInterval(POLL_MS)
        job.poll.timeout.connect(lambda: self._poll(job))
        self._jobs[job_id] = job
        process.finished.connect(lambda code, _status: self._exited(job, code))
        process.errorOccurred.connect(lambda error: self._error(job, error))
        process.started.connect(lambda: self._begin_telemetry(job))
        job.timer.start(job.window_ms)
        job.poll.start()
        process.start()

    def detach(self, job_id):
        """Stop watching a job without killing its process."""
        job = self._jobs.pop(job_id, None)
        if job is None or job.done:
            return
        job.done = True
        job.timer.stop()
        job.poll.stop()
        self._stop_telemetry(job)
        job.log.close()
        self._remove_capture(job)
        self._detached.append(job.process)

    def _begin_telemetry(self, job):
        if job.done or job.label not in TELEMETRY_LABELS or job.process.processId() <= 0:
            return
        job.sampler = TransferSampler(job.process.processId(), self)
        job.sampler.sample.connect(lambda data, job_id=job.id: self.telemetry.emit(job_id, data))
        job.sampler.start()

    def _stop_telemetry(self, job):
        if job.sampler is not None:
            job.sampler.stop()
            job.sampler = None

    def _read_new(self, job):
        try:
            with open(job.capture, "rb") as handle:
                handle.seek(job.offset)
                raw = handle.read()
        except FileNotFoundError:
            return ""
        job.offset += len(raw)
        if job.offset > CAPTURE_ROTATE_BYTES and not job.exited:
            # The child writes in append mode, so its next write lands at the new end (bounded log).
            os.truncate(job.capture, 0)
            job.offset = 0
        return raw.decode("utf-8", "replace")

    def _poll(self, job):
        if job.done:
            return
        chunk = self._read_new(job)
        if not chunk:
            return
        job.text = (job.text + chunk)[-OUTPUT_TAIL_LIMIT:]
        job.log.write(chunk)
        if job.inactivity and not job.timed_out:
            job.timer.start(job.window_ms)
        self.output.emit(job.id, chunk)

    def _exited(self, job, code):
        if job.done:
            return
        job.exited = True
        job.exit_code = code
        self._poll(job)  # final drain after the direct process exits
        self._complete(job, code, job.timed_out)

    def _error(self, job, error):
        if job.done or error != QProcess.ProcessError.FailedToStart:
            return
        job.text += "\nThe official launcher could not start."
        self._complete(job, None, False)

    def _timed_out(self, job):
        """Report uncertainty but keep the process, its polling and the busy lock until it really exits."""
        if job.done or job.timed_out:
            return
        job.timed_out = True
        job.timer.stop()
        self.timed_out.emit(job.id)

    def _remove_capture(self, job):
        try:
            job.capture.unlink()
        except FileNotFoundError:
            pass

    def _complete(self, job, exit_code, timed_out):
        job.done = True
        job.timer.stop()
        job.poll.stop()
        self._stop_telemetry(job)
        self._jobs.pop(job.id, None)
        job.log.close()
        self._remove_capture(job)
        job.process.deleteLater()
        self.finished.emit(job.id, {"exit_code": exit_code, "timed_out": timed_out, "output": job.text})


class FixtureRunner(QObject):
    """Smoke-test runner: answers from fixed text and never starts a process."""

    output = Signal(str, str)
    timed_out = Signal(str)
    finished = Signal(str, object)

    def __init__(self, responses, parent=None):
        super().__init__(parent)
        self._responses = dict(responses)

    def detached_count(self):
        return 0

    def start(self, job_id, argv, cwd, env, timeout_ms, inactivity=False, label="job"):
        command = next((part for part in argv if part in CLI_COMMANDS), "list")
        text = self._responses.get(command, "")
        QTimer.singleShot(0, lambda: self._deliver(job_id, text))

    def detach(self, job_id):
        return None

    def _deliver(self, job_id, text):
        if text:
            self.output.emit(job_id, text)
        self.finished.emit(job_id, {"exit_code": 0, "timed_out": False, "output": text})


SMOKE_RESPONSES = {
    "list": (
        "ID           Name            Version                        State       \n"
        "------------ --------------- ------------------------------ ------------\n"
        "beta         Open Beta                                      notinstalled\n"
        "diag         Homecoming (Diag)                                notinstalled\n"
        "live         Homecoming      Issue 28, Page 4 - 28.4.8623   installed   \n"
        "pre          Prerelease                                     notinstalled\n"
    ),
    "update": "Homecoming is up to date\n",
    "install": "Install succeeded\n",
    "launch": "",
}
