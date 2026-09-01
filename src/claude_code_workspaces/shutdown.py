"""Process-wide quit flag for worker threads (live reads, transcript scans).

An Event so the message loop can set it and workers can poll it.
"""

import threading

_requested = threading.Event()


def begin() -> None:
    _requested.set()


def requested() -> bool:
    return _requested.is_set()


def wait(seconds: float) -> bool:
    """Sleep up to `seconds`; True if quit was requested first. The interruptible `time.sleep` for worker threads."""
    return _requested.wait(seconds)


def reset() -> None:
    """Clear after `begin`. Needed when a second app runs in one process (tests)."""
    _requested.clear()
