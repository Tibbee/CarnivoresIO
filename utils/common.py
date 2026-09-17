import functools
import os
import tempfile
import time
import contextlib

from .logger import debug
from .performance import benchmark_session, benchmark_stage


@contextlib.contextmanager
def atomic_output_file(filepath):
    """Yield an open binary file that atomically replaces ``filepath``.

    Data streams to a temporary file in the destination directory. Only after
    the writer closes cleanly is the destination replaced with ``os.replace``.
    If the writer raises — disk full, I/O error, interruption — the temporary
    file is removed and the previous destination file is left intact.
    """
    filepath = os.fspath(filepath)
    directory = os.path.dirname(os.path.abspath(filepath))
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(filepath)}.",
        suffix=".tmp",
        dir=directory,
    )
    output = os.fdopen(fd, 'wb')
    try:
        yield output
        output.close()
        os.replace(temp_path, filepath)
    except BaseException:
        try:
            output.close()
        except Exception:
            pass
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def timed(label="Function", is_operator=False):
    """Log debug timing and feed decorated calls into an active benchmark."""
    def decorator(func):
        if is_operator:
            @functools.wraps(func)
            def wrapper(self, context, *args, **kwargs):
                start = time.perf_counter()
                try:
                    with benchmark_session(label):
                        return func(self, context, *args, **kwargs)
                finally:
                    debug(f"[Timing] {label} took {time.perf_counter() - start:.6f} seconds")
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start = time.perf_counter()
                try:
                    with benchmark_stage(label):
                        return func(*args, **kwargs)
                finally:
                    debug(f"[Timing] {label} took {time.perf_counter() - start:.6f} seconds")
        return wrapper
    return decorator
