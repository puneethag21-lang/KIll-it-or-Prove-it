"""
Official 1,000,000-Key Recovery Benchmark for Synterra KVDB.
Directives:
1. Test raw single-file log recovery (hard kill -9 after 1,000,000 records).
2. Assert strict single-file compliance (no .idx sidecar file on disk).
3. Assert recovery time < 2.0s (Section 5, Scenario F3).
"""

import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.db import KVDatabase
from src.storage import fsync_file


class Test1MKeyBenchmark(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "bench_1m.kv")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_1m_key_recovery_performance(self):
        total_keys = 1_000_000
        batch_size = 10_000
        num_batches = total_keys // batch_size

        print(f"\n=======================================================")
        print(f"BENCHMARK: Writing {total_keys:,} keys in {num_batches} batches of {batch_size:,}...")
        print(f"=======================================================")

        db = KVDatabase(self.db_path)
        t_write_start = time.perf_counter()

        for b in range(num_batches):
            base = b * batch_size
            ops = [
                ("put", f"k_{base + i}", f"val_{base + i}")
                for i in range(batch_size)
            ]
            db.batch(ops)

        t_write_end = time.perf_counter()
        write_duration = t_write_end - t_write_start
        print(f"Write completed in {write_duration:.2f}s ({total_keys / write_duration:,.0f} ops/sec)")

        # Verify size
        file_size = os.path.getsize(self.db_path)
        print(f"Database file size on disk: {file_size / (1024 * 1024):.2f} MB")

        # SIMULATE HARD KILL -9 / POWER YANK:
        # Flush raw OS buffers, close file descriptors directly WITHOUT writing .idx checkpoint cache
        print("\nSimulating unannounced kill -9 (power yank) on 1,000,000 records...")
        fsync_file(db.file)
        db.file.close()
        db.lock_file.close()
        db.closed = True

        # Strict Single-File Check: only bench_1m.kv and optional bench_1m.kv.lock exist
        data_files = [f for f in os.listdir(self.temp_dir.name) if not f.endswith(".lock")]
        self.assertEqual(data_files, ["bench_1m.kv"], "Strict single-file violation: unexpected files found on disk!")

        # 1. RAW UN-CHECKPOINTED SINGLE-FILE RECOVERY BENCHMARK (Scenario F3)
        print("\n[BENCHMARK] Executing RAW SINGLE-FILE log recovery (Cold Start after kill -9)...")
        t0 = time.perf_counter()
        db_recovered = KVDatabase(self.db_path)
        t1 = time.perf_counter()
        raw_recovery_time = t1 - t0

        print(f"--> RAW RECOVERY TIME: {raw_recovery_time:.4f}s")
        print(f"--> Active keys recovered: {len(db_recovered.index):,}")

        self.assertEqual(len(db_recovered.index), total_keys)
        self.assertLess(raw_recovery_time, 2.0, "Recovery exceeded PDF 2.0s boundary!")

        db_recovered.close()

        # Re-verify that after close(), STILL only the single .kv file exists!
        data_files_after = [f for f in os.listdir(self.temp_dir.name) if not f.endswith(".lock")]
        self.assertEqual(data_files_after, ["bench_1m.kv"], "Single-file invariant violated after close()!")
        print("=======================================================\n")


if __name__ == "__main__":
    unittest.main()
