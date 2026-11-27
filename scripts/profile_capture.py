#!/usr/bin/env python3
"""Private, durable observation of one original Linux child; never replay or signal."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

VERSION = 1
STARTUP_SECONDS = 15
MAX_BYTES = 1024 * 1024
_PROCESS_HASH_CACHE: OrderedDict = OrderedDict()
_HASH_LOCK = threading.RLock()


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _running_executable_digest(root: Path, birth: tuple) -> str:
    # Linux holds an executing inode against writes (ETXTBSY). This cache belongs
    # only to that original running process, never to mutable capture files.
    path = root / "exe"
    with _HASH_LOCK:
        before = path.stat()
        fingerprint = (
            *birth,
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        cached = _PROCESS_HASH_CACHE.get(fingerprint)
        if cached is not None:
            _PROCESS_HASH_CACHE.move_to_end(fingerprint)
            return cached
        value = hashlib.sha256(path.read_bytes()).hexdigest()
        after = path.stat()
        if fingerprint != (
            *birth,
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise OSError("original executable changed during hashing")
        _PROCESS_HASH_CACHE[fingerprint] = value
        if len(_PROCESS_HASH_CACHE) > 1024:
            _PROCESS_HASH_CACHE.popitem(last=False)
        return value


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def identity(pid: int) -> dict | None:
    root = Path("/proc") / str(pid)
    try:
        fields = (root / "stat").read_text().rpartition(") ")[2].split()
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        command = digest(root / "cmdline")
        executable = _running_executable_digest(root, (pid, fields[19], boot))
        if command != digest(root / "cmdline") and process_state(pid) != "Z":
            return None
        return {
            "pid": pid,
            "startTicks": fields[19],
            "bootId": boot,
            "commandSha256": command,
            "executableSha256": executable,
        }
    except (OSError, IndexError):
        return None


def process_state(pid: int) -> str | None:
    try:
        return (
            (Path("/proc") / str(pid) / "stat")
            .read_text()
            .rpartition(") ")[2]
            .split()[0]
        )
    except (OSError, IndexError):
        return None


def _spawn_receipt(pid: int, request: dict, request_hash: str) -> dict | None:
    """Retain a naturally exited, unreaped child owned by successful Popen exec.

    This is an explicit kernel spawn receipt, not an observed executable snapshot.
    The parent has not waited or reaped it, so its numeric PID cannot be reused.
    """
    try:
        fields = (
            (Path("/proc") / str(pid) / "stat").read_text().rpartition(") ")[2].split()
        )
        if fields[0] != "Z":
            return None
        return {
            "pid": pid,
            "startTicks": fields[19],
            "bootId": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "executionEvidence": "kernel_spawn_exec",
            "executionEvidenceVersion": 1,
            "requestSha256": request_hash,
            "requestedExecutableSha256": request["executableSha256"],
            "observedExecutableSha256": None,
        }
    except (OSError, IndexError):
        return None


def _read(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("capture evidence must be a regular file")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("capture evidence must be an object")
    return value


def unresolved(reason: str) -> dict:
    return {"status": "unresolved", "reason": reason, "submissionRepeated": False}


def execution_chain(executable: Path) -> list[dict]:
    """Bind Linux shebang selection without changing the command's arguments."""
    with executable.open("rb") as stream:
        header = stream.readline(256)
    if not header.startswith(b"#!"):
        return []
    fields = header[2:].decode("utf-8").strip().split(maxsplit=1)
    interpreter = Path(fields[0]).resolve(strict=True)
    chain = [interpreter]
    if interpreter == Path("/usr/bin/env").resolve(strict=True):
        if len(fields) != 2 or re.fullmatch(r"[A-Za-z0-9_.+-]+", fields[1]) is None:
            raise ValueError("unqualified env interpreter selection")
        selected = shutil.which(fields[1])
        if selected is None:
            raise FileNotFoundError(fields[1])
        chain.append(Path(selected).resolve(strict=True))
    for path in chain:
        with path.open("rb") as stream:
            if stream.read(2) == b"#!":
                raise ValueError("unqualified nested script interpreter")
    return [{"path": str(path), "sha256": digest(path)} for path in chain]


def resume(folder: Path, expected_argv: list[str] | None = None) -> dict:
    """Read the original receipt; never submit, signal, delete or reset a clock."""
    folder = Path(folder)
    try:
        if (
            folder.is_symlink()
            or folder.stat().st_uid != os.getuid()
            or folder.stat().st_mode & 0o077
        ):
            return unresolved("capture_directory_not_private")
        request = _read(folder / "request.private.json")
        binding = _read(folder / "binding.private.json")
        request_hash = digest(folder / "request.private.json")
        if request_hash != binding["requestSha256"]:
            return unresolved("request_changed")
        if expected_argv is not None and request["argv"] != expected_argv:
            return unresolved("expected_request_changed")
        if request["version"] != VERSION or request["collectorSha256"] != digest(
            folder / "collector.private.py"
        ):
            return unresolved("collector_changed")
        if digest(Path(request["executable"])) != request["executableSha256"]:
            return unresolved("executable_changed")
        if any(
            digest(Path(entry["path"])) != entry["sha256"]
            for entry in request.get("executionChain", [])
        ):
            return unresolved("interpreter_changed")
        worker = _read(folder / "worker.private.json")
        if worker != binding["worker"]:
            return unresolved("worker_binding_changed")
        terminal = folder / "terminal.private.json"
        if terminal.exists():
            result = _read(terminal)
            child = _read(folder / "child.private.json")
            if child.get("executionEvidence") == "kernel_spawn_exec":
                if (
                    child["requestSha256"] != request_hash
                    or child["requestedExecutableSha256"] != request["executableSha256"]
                    or child["observedExecutableSha256"] is not None
                ):
                    return unresolved("child_spawn_receipt_changed")
            elif (
                request.get("processExecutableSha256") is not None
                and child["executableSha256"] != request["processExecutableSha256"]
            ):
                return unresolved("child_executable_not_verified")
            if (
                result["requestSha256"] != request_hash
                or result["worker"] != worker
                or result["child"] != child
            ):
                return unresolved("terminal_binding_changed")
            if any(
                digest(folder / f"{name}.private.log") != result[name + "Sha256"]
                for name in ["stdout", "stderr"]
            ):
                return unresolved("output_changed")
            return result
        if identity(worker["pid"]) != worker:
            # Natural closure may publish the atomic receipt between the first
            # terminal-path check and this identity read. Revalidate that receipt.
            if terminal.exists():
                return resume(folder, expected_argv=expected_argv)
            return unresolved("original_worker_not_verified")
        if process_state(worker["pid"]) in {"T", "t"}:
            return unresolved("original_process_stopped")
        child_file = folder / "child.private.json"
        if child_file.exists():
            child = _read(child_file)
            observed = identity(child["pid"])
            # A child can close naturally just before its worker publishes a receipt.
            if observed is not None and observed != child:
                return unresolved("original_child_not_verified")
            if process_state(child["pid"]) in {"T", "t"}:
                return unresolved("original_process_stopped")
        else:
            child = None
        return {
            "status": "pending",
            "worker": worker,
            "child": child,
            "requestSha256": request_hash,
            "deadlineMonotonic": request["deadlineMonotonic"],
            "deadlineExpired": time.monotonic() >= request["deadlineMonotonic"],
            "submissionRepeated": False,
        }
    except (OSError, ValueError, KeyError, TypeError):
        return unresolved("capture_evidence_incomplete")


def wait(folder: Path, budget: float) -> dict:
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("positive finite observation budget required")
    deadline = time.monotonic() + budget
    result = resume(folder)
    while True:
        if result["status"] != "pending" or time.monotonic() >= deadline:
            return resume(folder)
        if (Path(folder) / "terminal.private.json").exists():
            return resume(folder)
        worker = result["worker"]
        if identity(worker["pid"]) != worker or process_state(worker["pid"]) in {
            "T",
            "t",
        }:
            return resume(folder)
        if result.get("child"):
            child = result["child"]
            observed = identity(child["pid"])
            if (
                observed is not None
                and observed != child
                or process_state(child["pid"]) in {"T", "t"}
            ):
                return resume(folder)
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))


def launch(
    folder: Path,
    argv: list[str],
    timeout: float,
    cwd: Path | None = None,
    max_bytes: int = MAX_BYTES,
    deadline_monotonic: float | None = None,
) -> dict:
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
    ):
        raise ValueError("exact nonempty argv required")
    if (
        not math.isfinite(timeout)
        or timeout <= 0
        or not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or max_bytes <= 0
    ):
        raise ValueError("positive finite limits required")
    if identity(os.getpid()) is None:
        raise RuntimeError("original Linux process identity unavailable")
    directory = Path(cwd or Path.cwd()).resolve(strict=True)
    executable_name = (
        shutil.which(argv[0]) if "/" not in argv[0] else str(directory / argv[0])
    )
    if executable_name is None:
        raise FileNotFoundError(argv[0])
    executable = Path(executable_name).resolve(strict=True)
    chain = execution_chain(executable)
    started = time.monotonic()
    deadline = started + timeout if deadline_monotonic is None else deadline_monotonic
    if not math.isfinite(deadline) or deadline <= started:
        raise ValueError("unexpired original deadline required")
    folder = Path(folder).absolute()
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir(mode=0o700)
    frozen_collector = folder / "collector.private.py"
    descriptor = os.open(frozen_collector, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(Path(__file__).read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    request = {
        "version": VERSION,
        "argv": argv,
        "cwd": str(directory),
        "startedEpoch": time.time(),
        "startedMonotonic": started,
        "deadlineMonotonic": deadline,
        "collectorSha256": digest(frozen_collector),
        "executable": str(executable),
        "executableSha256": digest(executable),
        "executionChain": chain,
        "processExecutableSha256": chain[-1]["sha256"] if chain else digest(executable),
        "maxBytes": max_bytes,
    }
    atomic_json(folder / "request.private.json", request)
    with (
        (folder / "worker.stdout.private.log").open("xb") as stdout,
        (folder / "worker.stderr.private.log").open("xb") as stderr,
    ):
        os.chmod(stdout.name, 0o600)
        os.chmod(stderr.name, 0o600)
        worker = subprocess.Popen(
            [sys.executable, "-B", str(frozen_collector), "--worker", str(folder)],
            cwd=directory,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    # Retain the original handle until natural closure. This observer never signals
    # a child, and a caller exit leaves the durable worker independently resumable.
    threading.Thread(target=worker.wait, daemon=True).start()
    deadline = min(started + STARTUP_SECONDS, deadline)
    while (
        not (folder / "binding.private.json").exists() and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    return resume(folder)


class ObservationIncomplete(RuntimeError):
    """The original observation cannot establish a complete outcome."""


class Observation:
    """Own a profiler's commands, original clock, cancellation and durable result."""

    def __init__(self, root: Path, config: dict, timeout: float, sources=()):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("positive finite original observation timeout required")
        owner = identity(os.getpid())
        if owner is None:
            raise ObservationIncomplete("original_observer_not_verified")
        # Validate before creating the durable lock. Never replace an existing one.
        config = json.loads(json.dumps(config, allow_nan=False))
        if not isinstance(config, dict):
            raise ValueError("observation config must be an object")
        source_bytes = [
            (str(Path(p).resolve(strict=True)), Path(p).read_bytes()) for p in sources
        ]
        self.root = Path(root).absolute()
        self.root.parent.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(mode=0o700)
        self.config = config
        self._lock = threading.RLock()
        self._closed = False
        self._incomplete = False
        started = time.monotonic()
        frozen = []
        for index, (path, data) in enumerate(source_bytes):
            target = self.root / f"source-{index:04d}.private"
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            frozen.append(
                {"originalPath": path, "file": target.name, "sha256": digest(target)}
            )
        self.request = {
            "version": VERSION,
            "config": config,
            "owner": owner,
            "startedEpoch": time.time(),
            "startedMonotonic": started,
            "deadlineMonotonic": started + timeout,
            "sources": frozen,
        }
        atomic_json(self.root / "observation.private.json", self.request)
        self.binding = {
            "owner": owner,
            "requestSha256": digest(self.root / "observation.private.json"),
        }
        atomic_json(self.root / "observer-binding.private.json", self.binding)
        atomic_json(self.root / "commands.private.json", {"commands": []})

    @property
    def deadline(self) -> float:
        return self.request["deadlineMonotonic"]

    @staticmethod
    def _binding(root: Path, config: dict) -> tuple[dict, dict]:
        if (
            root.is_symlink()
            or root.stat().st_uid != os.getuid()
            or root.stat().st_mode & 0o077
        ):
            raise ObservationIncomplete("observation_directory_not_private")
        request = _read(root / "observation.private.json")
        binding = _read(root / "observer-binding.private.json")
        if request["config"] != config:
            raise ObservationIncomplete("observation_config_changed")
        if binding["owner"] != request["owner"]:
            raise ObservationIncomplete("original_observer_not_verified")
        if binding["requestSha256"] != digest(root / "observation.private.json"):
            raise ObservationIncomplete("observation_request_changed")
        if request["version"] != VERSION or any(
            digest(root / source["file"]) != source["sha256"]
            for source in request["sources"]
        ):
            raise ObservationIncomplete("observation_source_changed")
        return request, binding

    @classmethod
    def inspect(cls, root: Path, config: dict) -> dict:
        """Inspect an original pending or final observation without submitting."""
        root = Path(root).absolute()
        try:
            request, binding = cls._binding(root, config)
            commands = _read(root / "commands.private.json")["commands"]
            names = [entry["folder"] for entry in commands]
            if names != [f"command-{index:06d}" for index in range(len(names))] or set(
                names
            ) != {path.name for path in root.glob("command-*")}:
                return unresolved("observation_commands_changed")
            captures = []
            for entry in commands:
                capture = resume(root / entry["folder"], expected_argv=entry["argv"])
                if (root / entry["folder"] / "request.private.json").exists() and _read(
                    root / entry["folder"] / "request.private.json"
                )["deadlineMonotonic"] != request["deadlineMonotonic"]:
                    return unresolved("observation_command_deadline_changed")
                captures.append(capture)
            terminal = root / "observation-terminal.private.json"
            if terminal.exists():
                outcome = _read(terminal)
                if outcome["binding"] != binding or outcome["commandsSha256"] != digest(
                    root / "commands.private.json"
                ):
                    return unresolved("observation_terminal_changed")
                output = Path(outcome["output"])
                if output.is_symlink() or digest(output) != outcome["outputSha256"]:
                    return unresolved("observation_output_changed")
                # A late child may close after an unresolved receipt. Never promote it.
                if outcome["result"] != "unresolved" and any(
                    c["status"] != "complete" for c in captures
                ):
                    return unresolved("observation_command_evidence_changed")
                return outcome
            if identity(binding["owner"]["pid"]) != binding["owner"]:
                return unresolved("original_observer_not_verified")
            if process_state(binding["owner"]["pid"]) in {"T", "t"}:
                return unresolved("original_observer_stopped")
            return {
                "status": "pending",
                "owner": binding["owner"],
                "commands": captures,
                "deadlineMonotonic": request["deadlineMonotonic"],
                "submissionRepeated": False,
            }
        except ObservationIncomplete as error:
            return unresolved(str(error))
        except (OSError, ValueError, KeyError, TypeError):
            return unresolved("observation_evidence_incomplete")

    @classmethod
    def request_cancel(cls, root: Path, config: dict) -> None:
        root = Path(root).absolute()
        request, binding = cls._binding(root, config)
        if (root / "observation-terminal.private.json").exists() or identity(
            binding["owner"]["pid"]
        ) != binding["owner"]:
            raise ObservationIncomplete("original_observer_not_verified")
        if process_state(binding["owner"]["pid"]) in {"T", "t"}:
            raise ObservationIncomplete("original_observer_stopped")
        control = {"binding": binding, "config": request["config"]}
        path = root / "cancel.private.json"
        # An exclusive marker grants one cooperative request; never overwrite it.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(control, sort_keys=True).encode())
            stream.flush()
            os.fsync(stream.fileno())

    def cancel_requested(self) -> bool:
        self._binding(self.root, self.config)
        path = self.root / "cancel.private.json"
        if not path.exists():
            return False
        if _read(path) != {"binding": self.binding, "config": self.config}:
            self._incomplete = True
            raise ObservationIncomplete("observation_cancellation_changed")
        return True

    def publish_marker(self, path: Path, *, status: str) -> None:
        """Publish one private readiness/owner marker without replacing foreign state."""
        self._binding(self.root, self.config)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(
                json.dumps(
                    {
                        "status": status,
                        "binding": self.binding,
                        "owner": self.binding["owner"],
                        "capture": str(self.root),
                    }
                ).encode()
            )
            stream.flush()
            os.fsync(stream.fileno())

    def start(
        self, argv: list[str], cwd: Path | None = None, *, allow_failure: bool = False
    ) -> Path:
        with self._lock:
            self._binding(self.root, self.config)
            if self.cancel_requested():
                raise ObservationIncomplete("observation_cancel_requested")
            if (
                self._incomplete
                or self._closed
                or (self.root / "observation-terminal.private.json").exists()
                or identity(self.binding["owner"]["pid"]) != self.binding["owner"]
                or time.monotonic() >= self.deadline
            ):
                self._incomplete = True
                raise ObservationIncomplete("observation_submission_closed")
            path = self.root / "commands.private.json"
            commands = _read(path)["commands"]
            folder = f"command-{len(commands):06d}"
            commands.append(
                {"folder": folder, "argv": argv, "allowFailure": allow_failure}
            )
            # Record intent before a child can exist; an interrupted launch stays unresolved.
            atomic_json(path, {"commands": commands})
            target = self.root / folder
            try:
                result = launch(
                    target,
                    argv,
                    self.deadline - time.monotonic(),
                    cwd=cwd,
                    deadline_monotonic=self.deadline,
                )
                if result["status"] == "unresolved":
                    raise ObservationIncomplete(result["reason"])
            except BaseException:
                self._incomplete = True
                raise
            return target

    def run(
        self, argv: list[str], cwd: Path | None = None, *, allow_failure: bool = False
    ) -> bytes:
        target = self.start(argv, cwd=cwd, allow_failure=allow_failure)
        remaining = self.deadline - time.monotonic()
        result = wait(target, remaining) if remaining > 0 else resume(target)
        if (
            result["status"] != "complete"
            or not result["withinDeadline"]
            or any(result["truncated"].values())
        ):
            self._incomplete = True
            raise ObservationIncomplete(
                result.get("reason", "original_command_incomplete")
            )
        stdout = (target / "stdout.private.log").read_bytes()
        if result["code"]:
            raise subprocess.CalledProcessError(
                result["code"],
                argv,
                output=stdout,
                stderr=(target / "stderr.private.log").read_bytes(),
            )
        return stdout

    def close(self, budget: float = 5) -> bool:
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError("positive finite natural closure budget required")
        self._closed = True
        end = time.monotonic() + budget
        while True:
            result = self.inspect(self.root, self.config)
            commands = _read(self.root / "commands.private.json")["commands"]
            captures = [
                resume(self.root / entry["folder"], entry["argv"]) for entry in commands
            ]
            closed = "reason" not in result and all(
                c["status"] == "complete"
                and all(
                    identity(c[role]["pid"]) is None for role in ("worker", "child")
                )
                for c in captures
            )
            if closed:
                return True
            if time.monotonic() >= end or any(
                c["status"] == "unresolved" for c in captures
            ):
                self._incomplete = True
                return False
            time.sleep(min(0.02, max(0, end - time.monotonic())))

    def finish(
        self, output: Path, *, code: int, result: str, observed_at: float | None = None
    ) -> dict:
        with self._lock:
            terminal = self.root / "observation-terminal.private.json"
            if terminal.exists():
                raise FileExistsError(terminal)
            current = self.inspect(self.root, self.config)
            commands = current.get("commands", [])
            observed_at = time.monotonic() if observed_at is None else observed_at
            invalid = (
                self._incomplete
                or not self._closed
                or current["status"] != "pending"
                or not math.isfinite(observed_at)
                or not self.request["startedMonotonic"] <= observed_at <= self.deadline
                or any(
                    c["status"] != "complete"
                    or not c.get("withinDeadline")
                    or any(c.get("truncated", {}).values())
                    for c in commands
                )
            )
            intents = _read(self.root / "commands.private.json")["commands"]
            if code == 0 and any(
                c.get("code", 1) != 0 and not intent["allowFailure"]
                for c, intent in zip(commands, intents)
            ):
                invalid = True
            output = Path(output).absolute()
            if output.is_symlink():
                raise ObservationIncomplete("observation_output_changed")
            outcome = {
                "status": "complete" if not invalid else "unresolved",
                "result": result if not invalid else "unresolved",
                "code": code if not invalid else (code or 2),
                "binding": self.binding,
                "commandsSha256": digest(self.root / "commands.private.json"),
                "output": str(output),
                "outputSha256": digest(output),
                "observedAtMonotonic": observed_at,
                "submissionRepeated": False,
            }
            atomic_json(terminal, outcome)
            return outcome


def _drain(stream, path: Path, limit: int, truncated: dict, name: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    retained = 0
    with os.fdopen(descriptor, "wb") as output:
        while chunk := stream.read1(65536):
            keep = chunk[: max(0, limit - retained)]
            if len(keep) != len(chunk):
                truncated[name] = True
            if keep:
                output.write(keep)
                output.flush()
                os.fsync(output.fileno())
                retained += len(keep)
    stream.close()


def worker_main(folder: Path) -> None:
    os.umask(0o077)
    request = _read(folder / "request.private.json")
    request_hash = digest(folder / "request.private.json")
    owner = identity(os.getpid())
    if owner is None or request["collectorSha256"] != digest(Path(__file__)):
        raise RuntimeError("original collector identity unavailable")
    if request["executableSha256"] != digest(Path(request["executable"])):
        raise RuntimeError("requested executable changed before submission")
    if any(
        digest(Path(entry["path"])) != entry["sha256"]
        for entry in request.get("executionChain", [])
    ):
        raise RuntimeError("requested interpreter changed before submission")
    atomic_json(folder / "worker.private.json", owner)
    atomic_json(
        folder / "binding.private.json",
        {"worker": owner, "requestSha256": request_hash},
    )
    if time.monotonic() >= request["deadlineMonotonic"]:
        raise RuntimeError("original deadline expired before child submission")
    child = subprocess.Popen(
        request["argv"],
        executable=request["executable"],
        cwd=request["cwd"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    original = identity(child.pid)
    startup_deadline = min(
        time.monotonic() + STARTUP_SECONDS, request["deadlineMonotonic"]
    )
    while original is None and time.monotonic() < startup_deadline:
        original = _spawn_receipt(child.pid, request, request_hash)
        if original is not None:
            break
        time.sleep(0.001)
        original = identity(child.pid)
    if original is None:
        # Keep the binding/lock and worker error; a guessed child cannot qualify.
        child.wait()
        raise RuntimeError("original child identity unavailable")
    atomic_json(folder / "child-start.private.json", original)
    first = original
    expected = request.get("processExecutableSha256", request["executableSha256"])
    allowed = {
        expected,
        *(entry["sha256"] for entry in request.get("executionChain", [])),
    }
    while (
        original.get("executionEvidence") != "kernel_spawn_exec"
        and original["executableSha256"] != expected
    ):
        if (
            any(original[key] != first[key] for key in ("pid", "startTicks", "bootId"))
            or original["executableSha256"] not in allowed
        ):
            child.wait()
            raise RuntimeError("original child execution identity changed")
        if time.monotonic() >= startup_deadline:
            child.wait()
            raise RuntimeError("original interpreter startup deadline expired")
        time.sleep(0.001)
        original = identity(child.pid)
        if original is None:
            original = _spawn_receipt(child.pid, request, request_hash)
            if original is None:
                child.wait()
                raise RuntimeError("original interpreter identity unavailable")
    atomic_json(folder / "child.private.json", original)
    truncated = {"stdout": False, "stderr": False}
    readers = []
    for name, stream in [("stdout", child.stdout), ("stderr", child.stderr)]:
        reader = threading.Thread(
            target=_drain,
            args=(
                stream,
                folder / f"{name}.private.log",
                request["maxBytes"],
                truncated,
                name,
            ),
        )
        reader.start()
        readers.append(reader)
    code = child.wait()
    finished = time.monotonic()
    for reader in readers:
        reader.join()
    if request_hash != digest(folder / "request.private.json") or request[
        "collectorSha256"
    ] != digest(Path(__file__)):
        raise RuntimeError("original evidence changed before closure")
    if request["executableSha256"] != digest(Path(request["executable"])) or any(
        digest(Path(entry["path"])) != entry["sha256"]
        for entry in request.get("executionChain", [])
    ):
        raise RuntimeError("original execution chain changed before closure")
    atomic_json(
        folder / "terminal.private.json",
        {
            "status": "complete",
            "code": code,
            "worker": owner,
            "child": original,
            "requestSha256": request_hash,
            "stdoutSha256": digest(folder / "stdout.private.log"),
            "stderrSha256": digest(folder / "stderr.private.log"),
            "finishedEpoch": time.time(),
            "finishedMonotonic": finished,
            "withinDeadline": finished <= request["deadlineMonotonic"],
            "truncated": truncated,
            "submissionRepeated": False,
        },
    )


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    worker_main(Path(sys.argv[2]))
