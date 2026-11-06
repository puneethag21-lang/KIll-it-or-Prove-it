"""
Unit tests for storage locking, fsync, and max_bytes enforcement.
"""

import os
import unittest
import tempfile
from src.storage import (
    open_lock_file, file_lock, fsync_file,
    check_max_bytes_or_raise
)
from src.errors import DiskFullError


class TestStorage(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = os.path.join(self.temp_dir.name, "test_store.kv")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_open_lock_file_creates_sentinel_byte(self):
        lock_f = open_lock_file(self.base_path)
        try:
            lock_f.seek(0, os.SEEK_END)
            self.assertGreaterEqual(lock_f.tell(), 1)
        finally:
            lock_f.close()

    def test_file_lock_context_manager(self):
        lock_f = open_lock_file(self.base_path)
        try:
            with file_lock(lock_f):
                # Critical section
                pass
        finally:
            lock_f.close()

    def test_max_bytes_enforcement(self):
        test_file = os.path.join(self.temp_dir.name, "data.bin")
        with open(test_file, "w+b") as f:
            f.write(b"12345")
            f.flush()

            # Limit is 10 bytes, current is 5 bytes
            # Adding 4 bytes -> total 9 <= 10: Should pass
            check_max_bytes_or_raise(f, 4, max_bytes=10)

            # Adding 6 bytes -> total 11 > 10: Must raise DiskFullError
            with self.assertRaises(DiskFullError):
                check_max_bytes_or_raise(f, 6, max_bytes=10)

            # None limit -> Should pass regardless
            check_max_bytes_or_raise(f, 1000000, max_bytes=None)

    def test_fsync(self):
        test_file = os.path.join(self.temp_dir.name, "fsync.bin")
        with open(test_file, "w+b") as f:
            f.write(b"durable data")
            fsync_file(f)


if __name__ == "__main__":
    unittest.main()

