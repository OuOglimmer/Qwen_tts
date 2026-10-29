"""Reusable background-task component shared by every studio domain.

One ``BackgroundJob`` instance per component serializes that component's
long-running work: at most one thread is alive, cancellation is cooperative,
and progress state is a single dict snapshot read by HTTP pollers.  Domain
components keep full control of the progress payload shape and of what their
runner does; this class only owns the lock/thread/cancel/progress plumbing
that used to be copy-pasted per feature.
"""

from __future__ import annotations

import threading
from typing import Any, Callable


class BackgroundJob:
    """A repeatable background task with cancel flag and progress snapshot.

    ``start`` refuses to run while a previous run is still alive, raising
    ``RuntimeError`` with ``busy_error`` (static text, or a callable for
    messages built from the live progress).  ``prepare`` runs inside the lock
    right before thread spawn, so validation and request-row creation stay
    atomic with the busy check.  Runners poll :attr:`cancel_requested`
    between work units and report via :meth:`update` / :meth:`finish`.
    """

    def __init__(self, name: str, done_key: str = "done") -> None:
        self._name = name
        self._done_key = done_key
        # Reentrant: a dynamic busy_error may read snapshot() while start()
        # still holds the lock.
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def clear_cancel(self) -> None:
        self._cancel.clear()

    def request_cancel(self) -> bool:
        """Ask the running task to stop; returns False when nothing runs."""
        running = self.running
        if running:
            self._cancel.set()
        return running

    def start(
        self,
        prepare: Callable[[], dict[str, Any]],
        runner: Callable[[], None],
        *,
        thread_name: str | Callable[[], str] = "",
        busy_error: str | Callable[[], str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError(self._busy_message(busy_error))
            self._cancel.clear()
            self._progress = dict(prepare())
            name = thread_name() if callable(thread_name) else thread_name
            self._thread = threading.Thread(
                target=self._guarded, args=(runner,), name=name or self._name, daemon=True
            )
            self._thread.start()
            return dict(self._progress)

    def _busy_message(self, busy_error: str | Callable[[], str] | None) -> str:
        if callable(busy_error):
            return busy_error()
        return busy_error or f"已有{self._name}任务正在运行，请等待完成或先取消"

    def _guarded(self, runner: Callable[[], None]) -> None:
        """Safety net: an escaping exception must still end the task."""
        try:
            runner()
        except Exception as exc:
            with self._lock:
                if self._progress is not None:
                    self._progress["error"] = f"{type(exc).__name__}: {exc}"
                    self._progress[self._done_key] = True

    def update(self, **fields: Any) -> None:
        with self._lock:
            if self._progress is not None:
                self._progress.update(fields)

    def finish(self, **fields: Any) -> None:
        with self._lock:
            if self._progress is not None:
                self._progress.update(fields)
                self._progress[self._done_key] = True

    def snapshot(self, *, add_running: bool = False) -> dict[str, Any] | None:
        with self._lock:
            if self._progress is None:
                return None
            state = dict(self._progress)
        if add_running:
            state["running"] = self.running
        return state
