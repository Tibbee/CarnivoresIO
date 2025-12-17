import time
import functools
from .logger import debug

def timed(label="Function", is_operator=False):
    def decorator(func):
        if is_operator:
            @functools.wraps(func)
            def wrapper(self, context, *args, **kwargs):
                start = time.perf_counter()
                result = func(self, context, *args, **kwargs)
                end = time.perf_counter()
                debug(f"[Timing] {label} took {end - start:.6f} seconds")
                return result
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start = time.perf_counter()
                result = func(*args, **kwargs)
                end = time.perf_counter()
                debug(f"[Timing] {label} took {end - start:.6f} seconds")
                return result
        return wrapper
    return decorator
