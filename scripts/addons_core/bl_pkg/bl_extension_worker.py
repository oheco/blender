# SPDX-FileCopyrightText: 2026 Oheco contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned, non-spawning Extensions transport for the OHOS CPython runtime.

Only Python I/O runs in workers. No bpy, main(), process-global stream, signal,
argv, environment or urllib opener changes. A fresh CLI namespace per command
preserves upstream parser/operations and isolates REQUEST_EXIT/FORCE_EXIT_OK.
DONE is a transport terminal, never a claim of successful installation.

close() cancels without joining the GUI thread. A supervisor owns the worker,
queue, deferred repository locks and (for abandoned consumers) a private JSONL
report. Call closed_reports() on the main thread, report the messages there,
then acknowledge_closed(id) to remove the report. No messages are dropped.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import json
import math
import os
import queue
import stat
import tempfile
import threading
import time
import types
import uuid
import zipfile
from collections.abc import Generator, Sequence
from typing import Any

QUEUE_LIMIT = 128
POLL_LIMIT = 32
SOCKET_TIMEOUT_MAX = 5.0
COMMAND_TIMEOUT = 120.0
BLOCKED_AFTER = 6.0
_ALLOWED_COMMANDS = {"sync", "list", "install-files", "install", "uninstall"}
_registry_lock = threading.RLock()
_active: dict[str, "Command"] = {}
_closed: dict[str, "Command"] = {}
_commands: dict[str, "Command"] = {}
# Only scalar path/cookie metadata crosses the worker/main-thread boundary.
# Held, deferred and failed releases remain visible until actual unlink succeeds.
_repo_locks: dict[str, dict[str, str | None]] = {}
_accepting = True


class CommandShuttingDown(RuntimeError):
    pass


def start_accepting() -> None:
    """Reopen admission only after the previous lifecycle is fully drained."""
    global _accepting
    with _registry_lock:
        if _commands or _closed or _active or _repo_locks:
            raise RuntimeError("Extensions shutdown ownership is still pending")
        _accepting = True


def request_shutdown(reason: str = "host shutdown", *, detach: bool = True) -> dict[str, Any]:
    """Close admission and cancel/detach every owned command, without waiting.

    The host must keep Python alive and poll ready after reliable report ack.
    """
    global _accepting
    with _registry_lock:
        _accepting = False
        commands = tuple(_commands.values())
    for command in commands:
        if detach:
            command.close()
        else:
            command.cancel()
    return shutdown_status()


def shutdown_status() -> dict[str, Any]:
    with _registry_lock:
        transport_ready = not _accepting and not (_commands or _closed or _active)
        return {"accepting": _accepting,
                "transport_ready": transport_ready,
                "ready": transport_ready and not _repo_locks,
                "commands": [command.snapshot() for command in _commands.values()],
                "pending_repo_locks": [dict(item, path=path) for path, item in _repo_locks.items()],
                "unacknowledged_reports": len(_closed)}


class CommandCancelled(BaseException):
    """Escape upstream broad Exception handlers while unwinding their contexts."""


class CommandDeadline(BaseException):
    pass


class CommandArgumentError(Exception):
    pass


def _key(directory: str) -> str:
    return os.path.normcase(os.path.realpath(directory))


def _release_lock(path: str, cookie: str) -> str | None:
    try:
        with open(path, encoding="utf8") as fh:
            owner = fh.read(16384)
        if owner != cookie:
            return "release(): lock was unexpectedly stolen by another program"
        os.unlink(path)
    except Exception as ex:
        return "release(): failed to release file ({!r})".format(ex)
    return None


def register_repo_lock(directory: str, path: str, cookie: str) -> None:
    with _registry_lock:
        previous = _repo_locks.get(path)
        if previous is not None and previous["cookie"] != cookie:
            raise RuntimeError("repository lock has a different retained owner")
        _repo_locks[path] = {"directory": directory, "cookie": cookie, "state": "held", "error": None}


def _repo_lock_released(path: str, cookie: str) -> None:
    with _registry_lock:
        item = _repo_locks.get(path)
        if item is not None and item["cookie"] == cookie:
            del _repo_locks[path]


def release_repo_lock_when_idle(directory: str, path: str, cookie: str) -> str | None:
    """Transfer to an active command or retain an immediate release failure."""
    with _registry_lock:
        item = _repo_locks.get(path)
        # acquire registered this exact lock; absence means a previous successful
        # release/retry. Do not unlink twice when its operator finish is retried.
        if item is None:
            return None
        if item["cookie"] != cookie:
            return "release(): repository lock has a different retained owner"
        command = _active.get(_key(directory))
        if command is not None:
            if (path, cookie) not in command.deferred_locks:
                command.deferred_locks.append((path, cookie))
            item["state"] = "deferred"
            return None
        item["state"] = "releasing"
    error = _release_lock(path, cookie)
    if error is None:
        _repo_lock_released(path, cookie)
    else:
        with _registry_lock:
            item["state"] = "error"
            item["error"] = error
    return error


def retry_repo_lock_cleanup(limit: int = 8) -> None:
    """One bounded main-thread retry of failed immediate releases; no joins."""
    with _registry_lock:
        pending = [(item["directory"], path, item["cookie"])
                   for path, item in _repo_locks.items() if item["state"] == "error"][:limit]
    for directory, path, cookie in pending:
        release_repo_lock_when_idle(directory, path, cookie)


def closed_reports() -> list[dict[str, Any]]:
    """Non-blocking metadata only. Read JSONL messages after report_complete.

    Entries are retained until explicit acknowledgment; caller must never treat
    cancelling/blocked/cleanup_error as done or release/re-enable GUI resources.
    """
    with _registry_lock:
        return [command.snapshot() | {"report_path": command.report_path}
                for command in _closed.values()]


def retry_closed_cleanup(command_id: str) -> None:
    """Retry an observed cleanup failure after its filesystem cause is fixed."""
    with _registry_lock:
        command = _closed[command_id]
    command.retry_cleanup()


def acknowledge_closed(command_id: str) -> None:
    with _registry_lock:
        command = _closed[command_id]
        if not command.report_complete.is_set() or command.threads_alive():
            raise RuntimeError("worker cleanup is still pending")
        if command.report_path:
            os.unlink(command.report_path)
        del _closed[command_id]
        _commands.pop(command_id, None)


class Command:
    def __init__(self, args: Sequence[str], cli_path: str, *,
                 queue_limit: int = QUEUE_LIMIT,
                 command_timeout: float = COMMAND_TIMEOUT,
                 start_paused: bool = False) -> None:
        self.id = uuid.uuid4().hex
        self.args = tuple(args)
        self.cli_path = cli_path
        if queue_limit < 1 or not math.isfinite(command_timeout) or command_timeout <= 0:
            raise ValueError("queue_limit and command_timeout must be positive and finite")
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue(queue_limit)
        self.cancel_event = threading.Event()
        self.dispatch_event = threading.Event()
        self.detached = threading.Event()
        self.cleanup_complete = threading.Event()
        self.report_complete = threading.Event()
        self.archive_thread: threading.Thread | None = None
        self.pending_report_message: tuple[str, Any] | None = None
        self.last_activity = self.started = time.monotonic()
        self.deadline = self.started + command_timeout
        self.outcome = "running"
        self.error_seen = False
        self.namespace: dict[str, Any] | None = None
        self.report_path: str | None = None
        self.cleanup_error: str | None = None
        self.report_error: str | None = None
        self.report_rollback_offset: int | None = None
        self.deferred_locks: list[tuple[str, str]] = []
        self.repo_key: str | None = None
        self.terminal_sent = False
        self.last_reported_wait_state: str | None = None
        self.worker = threading.Thread(target=self._run, name="extensions-" + self.id[:8], daemon=True)
        self.supervisor = threading.Thread(target=self._supervise, name="extensions-cleanup-" + self.id[:8], daemon=True)
        # Reserve repository ownership before returning control to the GUI.
        # Actual parsing, imports, compilation and all package I/O happen in worker.
        for i, value in enumerate(self.args):
            if value == "--local-dir" and i + 1 < len(self.args):
                self.repo_key = _key(self.args[i + 1])
                break
            if value.startswith("--local-dir="):
                self.repo_key = _key(value.partition("=")[2])
                break
        with _registry_lock:
            if not _accepting:
                raise CommandShuttingDown("Extensions shutdown rejects new commands")
            _commands[self.id] = self
            self.conflict = bool(self.repo_key and self.repo_key in _active)
            if self.repo_key and not self.conflict:
                _active[self.repo_key] = self
        self.worker.start()
        self.supervisor.start()
        if not start_paused:
            self.start()

    def checkpoint(self) -> None:
        if self.cancel_event.is_set():
            if self.namespace is not None:
                self.namespace["REQUEST_EXIT"] = True
            raise CommandCancelled()
        if time.monotonic() >= self.deadline:
            raise CommandDeadline()

    def _emit(self, ty: str, data: Any) -> None:
        # Take ownership of mutable message payloads; no serialization pipe.
        if isinstance(data, (list, tuple)):
            data = tuple(data)
        self.queue.put((ty, data))  # Backpressure occurs only in worker.
        self.last_activity = time.monotonic()

    def message_fn(self, ty: str, data: Any) -> bool:
        assert ty in self.namespace["MESSAGE_TYPES"]
        if ty == "DONE":
            # Upstream dummy/author commands can finish before thread cleanup.
            # Transport publishes exactly one terminal after supervisor cleanup.
            self.checkpoint()
            return False
        if ty in {"ERROR", "FATAL_ERROR"}:
            self.error_seen = True
        self._emit(ty, data)
        self.checkpoint()
        return False

    def _load_namespace(self) -> dict[str, Any]:
        ns: dict[str, Any] = {"__name__": "_blender_ext_owned_" + self.id,
                              "__file__": self.cli_path}
        # Reuse build-generated native bytecode when available. Parsing a large
        # CLI source file holds CPython's GIL even inside a worker; native package
        # assembly should compile this script with the bundled CPython version.
        loader = importlib.machinery.SourceFileLoader(ns["__name__"], self.cli_path)
        code = loader.get_code(ns["__name__"])
        if code is None:
            raise RuntimeError("unable to load upstream CLI code")
        exec(code, ns)
        self.namespace = ns
        ns["msglog_from_args"] = lambda _args: ns["MessageLogger"](self.message_fn)

        def force_exit_ok_enable() -> None:
            ns["FORCE_EXIT_OK"] = True  # Never replace sys.unraisablehook.
        ns["force_exit_ok_enable"] = force_exit_ok_enable

        class OwnedArgumentParser(argparse.ArgumentParser):
            def error(self, message: str) -> None:
                raise CommandArgumentError(message)

            def exit(self, status: int = 0, message: str | None = None) -> None:
                raise CommandArgumentError(message or "interactive CLI help is unavailable in worker")

            def _print_message(self, message: str, file: Any = None) -> None:
                raise CommandArgumentError(message)

        ns["argparse"] = types.SimpleNamespace(**vars(argparse))
        ns["argparse"].ArgumentParser = OwnedArgumentParser

        # Wrap upstream retrieval, keeping URL/headers/hash/size validation intact.
        # A zero/negative/NaN CLI timeout must not create an unbounded socket wait.
        retrieve = ns["url_retrieve_to_data_iter"]

        def owned_retrieve(url: str, **kwargs: Any) -> Generator[bytes, None, None]:
            self.checkpoint()
            timeout = kwargs["timeout_in_seconds"]
            if not math.isfinite(timeout) or timeout <= 0:
                timeout = SOCKET_TIMEOUT_MAX
            kwargs["timeout_in_seconds"] = min(timeout, SOCKET_TIMEOUT_MAX,
                                                max(0.001, self.deadline - time.monotonic()))
            for block in retrieve(url, **kwargs):
                self.checkpoint()
                self.last_activity = time.monotonic()
                yield block
            self.checkpoint()
        ns["url_retrieve_to_data_iter"] = owned_retrieve

        def owned_read(fh: Any, size: int, *, timeout_in_seconds: float) -> bytes:
            self.checkpoint()
            # HTTPResponse.read(size) may combine many socket reads; a peer
            # trickling bytes can keep that call alive past the command deadline.
            # read1 performs at most one underlying buffered read, exposing a
            # cancellation/deadline checkpoint after each received block.
            read = getattr(fh, "read1", fh.read)
            block = read(size)
            self.checkpoint()
            return block
        ns["read_with_timeout"] = owned_read

        # Check extraction between files and before chunks of large members.
        # Use the standard library extractor so upstream directory handling and
        # manifest checks still apply. Reject ambiguous traversal/symlink entries
        # before extracting or replacing any installed package.
        owner = self

        class OwnedZipFile(zipfile.ZipFile):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                try:
                    for member in self.infolist():
                        name = member.filename
                        components = name.rstrip("/").split("/")
                        if (name.startswith("/") or "\\" in name or ".." in components
                                or any(":" in part for part in components)
                                or stat.S_ISLNK(member.external_attr >> 16)):
                            raise ValueError("Unsafe archive path: {!r}".format(name))
                except BaseException:
                    self.close()
                    raise

            def extract(self, member: Any, path: Any = None, pwd: Any = None) -> str:
                owner.checkpoint()
                result = super().extract(member, path, pwd)
                owner.checkpoint()
                return result

        ns["zipfile"] = types.SimpleNamespace(**vars(zipfile))
        ns["zipfile"].ZipFile = OwnedZipFile
        # shutil.copyfileobj is shared: wrap the *owned* ZipExtFile reader instead
        # of changing shutil globally. Extraction reads at bounded chunk sizes.
        original_open = OwnedZipFile.open

        class OwnedReader:
            def __init__(self, stream: Any) -> None:
                self.stream = stream

            def __getattr__(self, name: str) -> Any:
                return getattr(self.stream, name)

            def __enter__(self) -> "OwnedReader":
                self.stream.__enter__()
                return self

            def __exit__(self, *args: Any) -> Any:
                return self.stream.__exit__(*args)

            def read(self, size: int = -1) -> bytes:
                owner.checkpoint()
                data = self.stream.read(size)
                owner.checkpoint()
                return data

        def owned_open(zip_self: Any, *args: Any, **kwargs: Any) -> Any:
            stream = original_open(zip_self, *args, **kwargs)
            return OwnedReader(stream)
        OwnedZipFile.open = owned_open
        return ns

    def _run(self) -> None:
        try:
            self.dispatch_event.wait()
            if self.conflict:
                raise CommandArgumentError("repository has an active in-process command")
            if not self.args or self.args[0] not in _ALLOWED_COMMANDS:
                raise CommandArgumentError("unsupported Extensions worker command")
            self.checkpoint()
            ns = self._load_namespace()
            parser = ns["argparse_create"](prog="blender_ext")
            args = parser.parse_args([*self.args, "--output-type=JSON_0"])
            if not hasattr(args, "func"):
                raise CommandArgumentError("missing Extensions command")
            self.checkpoint()
            result = args.func(args)
            if not isinstance(result, bool):
                raise TypeError("upstream command did not return a bool")
            self.checkpoint()
            self.outcome = "error" if self.error_seen or not result else "success"
            if not result and not self.error_seen:
                self._emit("ERROR", "Extensions command failed; worker cleanup will complete before DONE")
        except CommandCancelled:
            self.outcome = "cancelled"
            self._emit("ERROR", "Extensions command cancelled; completed filesystem changes are preserved")
        except CommandDeadline:
            self.outcome = "timeout"
            self._emit("FATAL_ERROR", "Extensions command exceeded its operation deadline")
        except BaseException as ex:
            self.outcome = "error"
            self._emit("FATAL_ERROR", "Extensions worker: {}: {}".format(type(ex).__name__, ex))
        finally:
            self.last_activity = time.monotonic()
            # No terminal is enqueued here: the supervisor must first join us,
            # release deferred locks and close the abandoned message report.

    def _supervise(self) -> None:
        try:
            self.worker.join()
            # Never hold the registry mutex during file I/O. A new deferred
            # release may arrive until the active entry is atomically removed.
            while True:
                with _registry_lock:
                    locks = self.deferred_locks[:]
                    self.deferred_locks.clear()
                    if not locks:
                        if self.repo_key and _active.get(self.repo_key) is self:
                            del _active[self.repo_key]
                        self.cleanup_complete.set()
                        return
                for index, (path, cookie) in enumerate(locks):
                    if error := _release_lock(path, cookie):
                        self.outcome = "cleanup_error"
                        self.cleanup_error = error
                        with _registry_lock:
                            self.deferred_locks[:0] = locks[index:]
                            if item := _repo_locks.get(path):
                                item["error"] = error
                        self._emit("ERROR", error)
                        # Repository ownership and pending lock releases remain
                        # retained. A failed cleanup must never produce DONE.
                        return
                    _repo_lock_released(path, cookie)
        except BaseException as ex:
            # Preserve ownership when cleanup fails: never fake DONE.
            self.cleanup_error = "{}: {}".format(type(ex).__name__, ex)

    def _archive(self) -> None:
        report = None
        try:
            if self.report_path is None:
                fd, self.report_path = tempfile.mkstemp(
                    prefix="blender-ext-closed-", suffix=".jsonl", dir=os.environ["TMPDIR"],
                )
                report = os.fdopen(fd, "w", encoding="utf8")
            else:
                report = open(self.report_path, "a+", encoding="utf8")
                if self.report_rollback_offset is not None:
                    report.seek(self.report_rollback_offset)
                    report.truncate()
            def write_record(record: dict[str, Any]) -> None:
                self.report_rollback_offset = report.tell()
                report.write(json.dumps(record) + "\n")
                report.flush()
                self.report_rollback_offset = None
            if self.pending_report_message is not None:
                write_record({"message": self.pending_report_message})
                self.pending_report_message = None
            while not self.cleanup_complete.is_set() or not self.queue.empty():
                try:
                    self.pending_report_message = self.queue.get(timeout=0.05)
                except queue.Empty:
                    if self.cleanup_error:
                        return
                    continue
                write_record({"message": self.pending_report_message})
                self.pending_report_message = None
            write_record({"terminal": self.outcome})
            report.close()
            report = None
            self.report_complete.set()
        except BaseException as ex:
            # Keep both the on-disk prefix and any dequeued, unwritten item.
            # A report failure is observable and cannot be acknowledged as done.
            self.report_error = "{}: {}".format(type(ex).__name__, ex)
        finally:
            if report is not None:
                try:
                    report.close()
                except BaseException as ex:
                    self.report_error = "{}: {}".format(type(ex).__name__, ex)

    def start(self) -> None:
        self.dispatch_event.set()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.dispatch_event.set()

    def close(self) -> None:
        self.cancel()
        with _registry_lock:
            if self.detached.is_set():
                return
            _closed[self.id] = self
            self.detached.set()
            self.archive_thread = threading.Thread(target=self._archive, daemon=True,
                                                   name="extensions-report-" + self.id[:8])
            self.archive_thread.start()

    def retry_cleanup(self) -> None:
        if self.report_error:
            if self.archive_thread is not None and self.archive_thread.is_alive():
                raise RuntimeError("report recovery is still running")
            self.report_error = None
            # Recover the consumer first even if its backpressure currently
            # blocks the worker. This allows cancellation/context cleanup to run.
            self.archive_thread = threading.Thread(target=self._archive, daemon=True)
            self.archive_thread.start()
            return
        if (self.worker.is_alive() or self.supervisor.is_alive()
                or (self.archive_thread is not None and self.archive_thread.is_alive())):
            raise RuntimeError("worker/report cleanup is still running")
        if not self.cleanup_error:
            raise RuntimeError("there is no observed cleanup failure to retry")
        self.cleanup_error = None
        if self.outcome == "cleanup_error":
            self.outcome = "error"
        if not self.cleanup_complete.is_set():
            self.supervisor = threading.Thread(target=self._supervise, daemon=True)
            self.supervisor.start()
        if self.detached.is_set():
            self.archive_thread = threading.Thread(target=self._archive, daemon=True)
            self.archive_thread.start()

    def threads_alive(self) -> bool:
        return (self.worker.is_alive() or self.supervisor.is_alive()
                or (self.archive_thread is not None and self.archive_thread.is_alive()))

    def snapshot(self) -> dict[str, Any]:
        blocked = time.monotonic() - self.last_activity >= BLOCKED_AFTER
        state = "cleanup_error" if self.cleanup_error or self.report_error else (
            self.outcome if self.cleanup_complete.is_set() else
            "blocked" if blocked else "cancelling" if self.cancel_event.is_set() else "running")
        return {"id": self.id, "state": state, "outcome": self.outcome,
                "worker_alive": self.worker.is_alive(), "threads_alive": self.threads_alive(),
                "cleanup_complete": self.cleanup_complete.is_set(),
                "report_complete": self.report_complete.is_set(),
                "queue_size": self.queue.qsize(), "cleanup_error": self.cleanup_error,
                "report_error": self.report_error}

    def poll(self) -> tuple[list[tuple[str, Any]], bool]:
        result = []
        if not self.detached.is_set():
            for _ in range(POLL_LIMIT):
                try:
                    result.append(self.queue.get_nowait())
                except queue.Empty:
                    break
        state = self.snapshot()["state"]
        if state in {"blocked", "cleanup_error"} and state != self.last_reported_wait_state:
            result.append(("STATUS", "Extensions worker {}: cleanup is still pending".format(state)))
            self.last_reported_wait_state = state
        with _registry_lock:
            finished = (self.cleanup_complete.is_set() and not self.threads_alive()
                        and self.queue.empty() and not self.cleanup_error and not self.report_error
                        and (not self.detached.is_set() or self.id not in _closed))
            if finished and not self.terminal_sent:
                self.terminal_sent = True
                _commands.pop(self.id, None)
                result.append(("DONE", ""))
        return result, finished


def command_output(args: Sequence[str], use_idle: bool, *, cli_path: str
                   ) -> Generator[Sequence[tuple[str, Any]], bool, None]:
    command = Command(args, cli_path, start_paused=True)
    finished = False
    try:
        # Return the first GUI invocation before starting CLI initialization.
        # This also accepts an immediate cancel without loading any CLI source.
        request_exit = yield []
        if request_exit:
            command.cancel()
        else:
            command.start()
        while not finished:
            messages, finished = command.poll()
            request_exit = yield messages
            if request_exit:
                command.cancel()
            if use_idle and not messages and not finished:
                # Blocking command-line consumers explicitly opt in; the GUI
                # passes use_idle=False and never waits on the worker.
                command.cleanup_complete.wait(0.05)
    finally:
        if not finished:
            command.close()
