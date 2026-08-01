import functools
import time

from .logger import debug
from .performance import benchmark_session, benchmark_stage


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
