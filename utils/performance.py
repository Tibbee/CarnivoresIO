"""Opt-in structured performance instrumentation for Blender operations."""

from __future__ import annotations

import contextvars
import json
import platform
import time
from contextlib import contextmanager

import bpy

from .addon import get_addon_preferences
from .logger import info, warn


_REPORT_TEXT_NAME = "Carnivores_Performance_Report"
_current_session = contextvars.ContextVar("carnivores_performance_session", default=None)


def performance_enabled():
    try:
        prefs = get_addon_preferences(__package__)
        return bool(getattr(prefs, "performance_mode", False))
    except Exception:
        return False


def current_session():
    return _current_session.get()


class BenchmarkSession:
    """Collect nested wall-clock stages and emit one JSON-backed report."""

    def __init__(self, operation, metadata=None, enabled=None):
        self.operation = str(operation)
        self.enabled = performance_enabled() if enabled is None else bool(enabled)
        self.metadata = dict(metadata or {})
        self.records = []
        self._stack = []
        self._start_ns = None
        self._token = None

    def __enter__(self):
        if self.enabled:
            self._start_ns = time.perf_counter_ns()
            self._token = _current_session.set(self)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if not self.enabled:
            return False
        total_ns = time.perf_counter_ns() - self._start_ns
        if self._token is not None:
            _current_session.reset(self._token)
        self._emit(total_ns, exc)
        return False

    def add_metadata(self, **values):
        if self.enabled:
            self.metadata.update(values)

    def record_duration(self, name, duration_seconds, status="ok", **metadata):
        """Record a stage measured by code that already owns a timer."""
        if not self.enabled:
            return
        label = str(name)
        path = ".".join((*self._stack, label))
        self.records.append({
            "stage": path,
            "duration_ms": float(duration_seconds) * 1000.0,
            "status": str(status),
            "error": None,
            "metadata": metadata,
        })

    @contextmanager
    def stage(self, name, **metadata):
        if not self.enabled:
            yield
            return

        label = str(name)
        path = ".".join((*self._stack, label))
        self._stack.append(label)
        start_ns = time.perf_counter_ns()
        failure = None
        try:
            yield
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            duration_ns = time.perf_counter_ns() - start_ns
            self._stack.pop()
            self.records.append({
                "stage": path,
                "duration_ms": duration_ns / 1_000_000.0,
                "status": "failed" if failure else "ok",
                "error": failure,
                "metadata": metadata,
            })

    def _emit(self, total_ns, operation_error):
        payload = {
            "schema_version": 1,
            "operation": self.operation,
            "status": "failed" if operation_error else "ok",
            "error": (
                f"{type(operation_error).__name__}: {operation_error}"
                if operation_error else None
            ),
            "total_ms": total_ns / 1_000_000.0,
            "metadata": {
                "blender_version": bpy.app.version_string,
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                **self.metadata,
            },
            "stages": self.records,
        }
        serialized = json.dumps(payload, indent=2, sort_keys=True, default=str)

        try:
            text = bpy.data.texts.get(_REPORT_TEXT_NAME)
            if text is None:
                text = bpy.data.texts.new(_REPORT_TEXT_NAME)
            text.clear()
            text.write(serialized)
        except Exception as exc:
            warn(f"Could not write performance report datablock: {exc}")

        info(f"[Performance] {self.operation}: {payload['total_ms']:.3f} ms")
        for record in sorted(self.records, key=lambda item: item["duration_ms"], reverse=True):
            info(
                f"[Performance]   {record['stage']}: "
                f"{record['duration_ms']:.3f} ms ({record['status']})"
            )


def benchmark_session(operation, metadata=None):
    return BenchmarkSession(operation, metadata=metadata)


@contextmanager
def benchmark_stage(name, **metadata):
    session = current_session()
    if session is None or not session.enabled:
        yield
        return
    with session.stage(name, **metadata):
        yield
