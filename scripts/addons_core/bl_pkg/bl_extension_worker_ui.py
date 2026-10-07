# SPDX-FileCopyrightText: 2026 Oheco contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread Extensions lifecycle owner; bpy is injected only at register.

No UI callback joins a thread or waits for network. The native host must call
request_shutdown(), then continue main-thread pump() until shutdown_status()
returns ready=True before PyFinalize. The atexit fallback only requests cancel.
"""
from __future__ import annotations
import atexit
import json
import os
import threading
from typing import Any
from . import bl_extension_worker as worker

INTERVAL = 0.05
MESSAGE_LIMIT = 32
_owner: "MainThreadOwner | None" = None


def _assert_main() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Extensions bpy lifecycle must run on the main thread")


def _atexit_cancel() -> None:
    # Python 3.13 forbids starting new threads in atexit. This fallback cannot
    # drain a failed/stalled lifecycle; an early host shutdown is mandatory.
    worker.request_shutdown("interpreter exit fallback", detach=False)


def ensure_register_ready() -> None:
    _assert_main()
    if _owner is not None and _owner.shutdown_requested and not _owner.ready():
        raise RuntimeError("previous Extensions teardown is still owned")


def register(bpy: Any, status: Any, *, notify_shutdown: Any = None) -> None:
    global _owner
    _assert_main()
    if _owner is not None and not _owner.shutdown_requested:
        return
    if _owner is not None and not _owner.ready():
        raise RuntimeError("previous Extensions lifecycle is still owned")
    worker.start_accepting()
    _owner = MainThreadOwner(bpy, status, notify_shutdown)
    _owner.ensure_timer()
    atexit.unregister(_atexit_cancel)
    atexit.register(_atexit_cancel)


def track_operator(op: Any, handle: Any) -> None:
    _assert_main()
    if _owner is None:
        raise RuntimeError("Extensions lifecycle hook is not registered")
    _owner.operators[id(op)] = (op, handle)


def untrack_operator(op: Any) -> None:
    _assert_main()
    if _owner is not None:
        _owner.operators.pop(id(op), None)
        _owner.pending_cancel.discard(id(op))
        _owner.errors.pop("operator-" + str(id(op)), None)


def defer_operator_retry(op: Any, handle: Any, error: Exception) -> None:
    _assert_main()
    track_operator(op, handle)
    if handle.terminal_result is None:
        handle.request_exit = True
    _owner.pending_cancel.add(id(op))
    _owner.errors["operator-" + str(id(op))] = "{}: {}".format(type(error).__name__, error)
    _owner.ensure_timer()


def defer_operator_cancel(op: Any, handle: Any) -> None:
    _assert_main()
    track_operator(op, handle)
    handle.request_exit = True
    _owner.pending_cancel.add(id(op))
    _owner.ensure_timer()


def track_notify_batch(batch: Any, *, staging_paths: tuple[str, ...] = ()) -> None:
    _assert_main()
    if _owner is not None:
        _owner.notify_batches.add(batch)
        _owner.notify_paths_by_batch[batch] = staging_paths


def untrack_notify_batch(batch: Any) -> None:
    _assert_main()
    if _owner is not None:
        _owner.notify_batches.discard(batch)
        # Upstream logs rename/remove failures but can still finish its sync
        # generator. Preserve any surviving owned temporary index for shutdown.
        for path in _owner.notify_paths_by_batch.pop(batch, ()):
            if os.path.lexists(path):
                _owner.pending_notify_paths.add(path)


def request_shutdown(reason: str = "native host shutdown", *, teardown: Any = None) -> dict[str, Any]:
    global _owner
    _assert_main()
    if _owner is None:
        # A creator may use the transport without enabling the addon. Keep a
        # host-pumped, main-thread status owner; no bpy import/timer is needed.
        from . import repo_status_text
        _owner = MainThreadOwner(None, repo_status_text, None)
    _owner.begin_shutdown(reason, teardown)
    return shutdown_status()


def request_unregister(teardown: Any) -> bool:
    """Return True when the existing addon teardown body is deferred/owned."""
    was_ready = _owner is not None and _owner.ready()
    request_shutdown("addon unregister", teardown=teardown)
    if was_ready:
        # A creator that already drained may now disable addons synchronously.
        # Otherwise the registered callback/host pump owns this teardown.
        pump()
    return True


def shutdown_status() -> dict[str, Any]:
    _assert_main()
    result = worker.shutdown_status()
    if _owner is not None:
        result |= {"ready": _owner.ready(), "pending_operators": len(_owner.operators),
                   "pending_delivery": len(_owner.cursors),
                   "pending_notify_staging": sorted(_owner.pending_notify_paths),
                   "hook_errors": dict(_owner.errors),
                   "teardown_complete": _owner.teardown_complete}
    return result


def pump() -> dict[str, Any]:
    """One bounded host tick, also usable when Blender timers are suspended."""
    _assert_main()
    if _owner is not None:
        if _owner._timer() is None and _owner.bpy is not None:
            if _owner.bpy.app.timers.is_registered(_owner.timer):
                _owner.bpy.app.timers.unregister(_owner.timer)
    return shutdown_status()


def delivered_log() -> list[tuple[str, tuple[str, Any]]]:
    """Native creator can copy the delivered audit into its own log before exit."""
    _assert_main()
    return list(_owner.delivered_records) if _owner is not None else []


def retry_cleanup(command_id: str) -> None:
    """Explicit retry after repairing the reported filesystem/delivery cause."""
    _assert_main()
    worker.retry_closed_cleanup(command_id)
    if _owner is not None:
        _owner.errors.pop(command_id, None)
        _owner.ensure_timer()


class MainThreadOwner:
    def __init__(self, bpy: Any, status: Any, notify_shutdown: Any) -> None:
        self.bpy = bpy
        self.status = status
        self.notify_shutdown = notify_shutdown
        self.operators: dict[int, tuple[Any, Any]] = {}
        self.pending_cancel: set[int] = set()
        self.notify_batches: set[Any] = set()
        self.notify_paths_by_batch: dict[Any, tuple[str, ...]] = {}
        self.pending_notify_paths: set[str] = set()
        self.cursors: dict[str, dict[str, Any]] = {}
        self.errors: dict[str, str] = {}
        self.wait_states: dict[str, str] = {}
        # Messages are delivered to real StatusInfoUI and also retained in this
        # main-thread audit list after journal acknowledgment. UI may clear its
        # status log later; that does not erase an unacknowledged owned message.
        self.delivered_records: list[tuple[str, tuple[str, Any]]] = []
        self.shutdown_requested = False
        self.teardown = None
        self.teardown_complete = False
        self.timer = self._timer

    def ensure_timer(self) -> None:
        _assert_main()
        if self.bpy is None:
            return  # The native creator calls pump() until ready.
        if not self.bpy.app.timers.is_registered(self.timer):
            self.bpy.app.timers.register(self.timer, first_interval=INTERVAL, persistent=True)

    def begin_shutdown(self, reason: str, teardown: Any) -> None:
        _assert_main()
        if self.ready() and (teardown is None or self.teardown is teardown):
            return
        self.shutdown_requested = True
        if teardown is not None:
            if self.teardown is not None and self.teardown is not teardown:
                raise RuntimeError("a different addon teardown is already owned")
            self.teardown = teardown
        # Admission closes before any consumer or UI callback is removed.
        worker.request_shutdown(reason)
        for op_id, (_op, handle) in self.operators.items():
            handle.request_exit = True
            self.pending_cancel.add(op_id)
        for batch in tuple(self.notify_batches):
            self.pending_notify_paths.update(self.notify_paths_by_batch.pop(batch, ()))
            batch.close_iterators()
        self.notify_batches.clear()
        if self.notify_shutdown is not None:
            self.notify_shutdown()
        self.ensure_timer()

    def deliver(self, command_id: str, message: tuple[str, Any]) -> None:
        _assert_main()
        ty, data = message
        if ty == "PROGRESS":
            data = tuple(data)
        else:
            data = str(data)
        self.status.title = "Extensions background cleanup"
        self.status.log.append((ty, data))
        self.delivered_records.append((command_id, (ty, data)))

    def consume_reports(self) -> None:
        remaining = MESSAGE_LIMIT
        for report in worker.closed_reports():
            command_id = report["id"]
            if not report["report_complete"] or report["threads_alive"]:
                if error := report["cleanup_error"] or report["report_error"]:
                    if self.errors.get(command_id) != error:
                        self.deliver(command_id, ("ERROR", "Extensions cleanup pending: " + error))
                    self.errors[command_id] = error
                elif report["state"] == "blocked" and self.wait_states.get(command_id) != "blocked":
                    self.deliver(command_id, ("STATUS", "Extensions worker blocked; ownership is retained"))
                    self.wait_states[command_id] = "blocked"
                continue
            cursor = self.cursors.setdefault(command_id, {"stream": None, "pending": None,
                                                         "terminal": False, "eof": False})
            try:
                if cursor["stream"] is None and not cursor["eof"]:
                    cursor["stream"] = open(report["report_path"], encoding="utf8")
                while not cursor["eof"] and remaining:
                    if cursor["pending"] is None:
                        offset = cursor["stream"].tell()
                        try:
                            line = cursor["stream"].readline()
                            if not line:
                                if not cursor["terminal"]:
                                    raise RuntimeError("closed report lacks an actual terminal")
                                cursor["stream"].close()
                                cursor["stream"] = None
                                cursor["eof"] = True
                                break
                            record = json.loads(line)
                            if not isinstance(record, dict) or not ({"message", "terminal"} & record.keys()):
                                raise RuntimeError("invalid closed report record")
                        except Exception:
                            cursor["stream"].seek(offset)
                            raise
                        cursor["pending"] = record
                    record = cursor["pending"]
                    if "message" in record:
                        self.deliver(command_id, tuple(record["message"]))
                    elif "terminal" in record:
                        self.deliver(command_id, ("STATUS", "Extensions worker completed: " + record["terminal"]))
                        cursor["terminal"] = True
                    else:
                        raise RuntimeError("invalid closed report record")
                    cursor["pending"] = None  # Commit cursor only after delivery.
                    remaining -= 1
                if cursor["eof"]:
                    worker.acknowledge_closed(command_id)
                    del self.cursors[command_id]
                    self.errors.pop(command_id, None)
                    self.wait_states.pop(command_id, None)
            except Exception as ex:
                # Keep the open cursor/pending record and original journal.
                # A later tick can retry delivery or journal removal.
                self.errors[command_id] = "{}: {}".format(type(ex).__name__, ex)
            if not remaining:
                break

    def cleanup_notify_staging(self) -> None:
        # Never unlink a temp index while a worker can still write it. Admission
        # is closed; all reports were delivered/acked and every thread exited.
        if not self.shutdown_requested or not worker.shutdown_status()["transport_ready"]:
            return
        for path in tuple(self.pending_notify_paths)[:MESSAGE_LIMIT]:
            try:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                self.pending_notify_paths.discard(path)
                self.errors.pop("notify-staging-" + path, None)
            except Exception as ex:
                self.errors["notify-staging-" + path] = "{}: {}".format(type(ex).__name__, ex)

    def finish_operators(self) -> None:
        # Full shutdown archives all queues first. Its finish callbacks may use
        # bpy and release upstream locks only after reports are delivered/acked.
        if self.shutdown_requested and (not worker.shutdown_status()["transport_ready"] or self.pending_notify_paths):
            return
        for op_id in tuple(self.pending_cancel)[:8]:
            pair = self.operators.get(op_id)
            if pair is None:
                self.pending_cancel.discard(op_id)
                continue
            op, handle = pair
            try:
                result = handle.op_modal_step(op, self.bpy.context)
                if "RUNNING_MODAL" in result:
                    continue
                handle.op_finish_owned(op, self.bpy.context)
                # op_finish_owned only untracks after all cleanup stages commit.
            except Exception as ex:
                # Keep the operator/finish resources even if Blender rejects a
                # now-invalid context; never claim teardown on that error.
                self.operators[op_id] = pair
                self.errors["operator-" + str(op_id)] = "{}: {}".format(type(ex).__name__, ex)

    def ready(self) -> bool:
        return (self.shutdown_requested and worker.shutdown_status()["ready"]
                and not self.operators and not self.cursors and not self.errors and not self.pending_notify_paths
                and (self.teardown is None or self.teardown_complete))

    def tick(self) -> None:
        _assert_main()
        before = (len(self.delivered_records), self.status.running)
        self.consume_reports()
        worker.retry_repo_lock_cleanup()
        self.cleanup_notify_staging()
        self.finish_operators()
        drained = (self.shutdown_requested and worker.shutdown_status()["ready"]
                   and not self.operators and not self.cursors and not self.errors and not self.pending_notify_paths)
        if drained and self.teardown is not None and not self.teardown_complete:
            try:
                self.teardown()
                self.teardown_complete = True
            except Exception as ex:
                self.errors["teardown"] = "{}: {}".format(type(ex).__name__, ex)
        self.status.running = bool(worker.closed_reports() or worker.shutdown_status()["pending_repo_locks"]
                                   or self.operators or self.cursors or self.errors
                                   or (self.shutdown_requested and self.pending_notify_paths))
        if self.bpy is None or self.teardown_complete or before == (len(self.delivered_records), self.status.running):
            return
        for window in self.bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "PREFERENCES":
                    area.tag_redraw()

    def _timer(self) -> float | None:
        _assert_main()
        self.errors.pop("timer", None)
        try:
            self.tick()
        except Exception as ex:
            # Keep the consumer alive and block readiness after any callback
            # failure; a subsequent bounded tick retries without journal loss.
            self.errors["timer"] = "{}: {}".format(type(ex).__name__, ex)
        if self.ready():
            atexit.unregister(_atexit_cancel)
            return None
        return INTERVAL
