"""
Storage primitives, cross-process locking, fsync, and disk-full enforcement.
"""

import os
import threading
from contextlib import contextmanager
from src.errors import DiskFullError

# Global re-entrant lock protecting intra-process concurrent threads
_GLOBAL_THREAD_LOCK = threading.RLock()


@contextmanager
def file_lock(lock_file_obj):
    """
    Acquires an exclusive cross-process lock on lock_file_obj,
    combined with an intra-process threading lock.
    """
    with _GLOBAL_THREAD_LOCK:
        lock_file_obj.seek(0)
        fileno = lock_file_obj.fileno()

        if os.name == "nt":
            import msvcrt
            # Lock the first byte exclusively
            msvcrt.locking(fileno, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fileno, fcntl.LOCK_EX)

        try:
            yield
        finally:
            lock_file_obj.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(fileno, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(fileno, fcntl.LOCK_UN)
                except OSError:
                    pass


def open_lock_file(base_path: str):
    """
    Opens and initializes the companion lock file (<base_path>.lock).
    Guarantees at least 1 byte exists so Windows msvcrt.locking never fails.
    """
    lock_path = base_path + ".lock"
    # Open in append + binary mode
    f = open(lock_path, "a+b")
    f.seek(0, os.SEEK_END)
    if f.tell() == 0:
        f.write(b"\0")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    return f


def fsync_file(file_obj):
    """
    Flushes Python userspace buffers and issues os.fsync to force
    kernel pages to durable physical storage.
    """
    file_obj.flush()
    os.fsync(file_obj.fileno())


def check_max_bytes_or_raise(file_obj, bytes_to_add: int, max_bytes: int):
    """
    Verifies whether adding bytes_to_add will exceed max_bytes.
    Must be called BEFORE any write occurs.
    Raises DiskFullError if limit would be breached.
    """
    if max_bytes is None:
        return

    file_obj.seek(0, os.SEEK_END)
    current_size = file_obj.tell()

    if current_size + bytes_to_add > max_bytes:
        raise DiskFullError(
            f"Write failed: operation requires {bytes_to_add} bytes, "
            f"current size is {current_size} bytes, limit is {max_bytes} bytes"
        )


def sync_directory_if_posix(file_path: str):
    """
    On POSIX systems, fsyncs the parent directory after rename.
    On Windows (NTFS), directory fsync is not supported by the OS and is a no-op;
    Windows NTFS guarantees atomic metadata update during os.replace.
    """
    if os.name != "nt":
        dir_path = os.path.dirname(os.path.abspath(file_path)) or "."
        try:
            dfd = os.open(dir_path, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass

