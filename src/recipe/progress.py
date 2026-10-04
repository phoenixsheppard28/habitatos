import logging
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from uuid import uuid4

logger = logging.getLogger(__name__)
current_tracker = ContextVar("pipeline_progress", default=None)


class ProgressTracker:
    def __init__(self, callback=None):
        self.callback = callback
        self.request_id = uuid4().hex
        self.started = monotonic()
        self.timings = []

    def emit(self, stage, message, status, *, identity=None, duration_seconds=None, **details):
        event = {"id": identity or uuid4().hex, "request_id": self.request_id,
                 "stage": stage, "message": message, "status": status,
                 "elapsed_seconds": round(monotonic() - self.started, 3)}
        if duration_seconds is not None:
            event["duration_seconds"] = round(duration_seconds, 3)
            self.timings.append(event)
        if details:
            event["details"] = details

        logger.info("pipeline request=%s stage=%s status=%s elapsed=%.3fs duration=%s message=%s",
                    self.request_id, stage, status, event["elapsed_seconds"], duration_seconds, message)
        if self.callback:
            self.callback(event)

        return event["id"]


@contextmanager
def progress_session(callback=None):
    tracker = ProgressTracker(callback)
    token = current_tracker.set(tracker)
    try:
        yield tracker
    finally:
        current_tracker.reset(token)


@contextmanager
def stage(name, message, **details):
    tracker = current_tracker.get()
    if tracker is None:
        yield
        return

    identity = tracker.emit(name, message, "running", **details)
    started = monotonic()
    try:
        yield
    except Exception:
        tracker.emit(name, message, "error", identity=identity,
                     duration_seconds=monotonic() - started, **details)
        raise
    else:
        tracker.emit(name, message, "complete", identity=identity,
                     duration_seconds=monotonic() - started, **details)


def notify(name, message, **details):
    tracker = current_tracker.get()
    if tracker:
        tracker.emit(name, message, "running", **details)
