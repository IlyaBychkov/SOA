from collections.abc import Callable, Generator
from contextlib import contextmanager
from threading import Event, Thread


@contextmanager
def worker(run: Callable[..., None], *args: object) -> Generator[None, None, None]:
    """Run a function in a thread, passing the stop event as its last argument."""
    stop = Event()
    thread = Thread(target=run, args=(*args, stop))
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
