"""
Official 1,000,000-Key Recovery Benchmark for Synterra KVDB.
Directives:
1. Test raw un-checkpointed recovery (hard kill -9 after 1,000,000 records).
2. Test checkpoint-accelerated recovery.
3. Assert recovery time < 0.50s (0.40s - 0.49s).
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

        # Ensure NO .idx cache exists
        idx_path = self.db_path + ".idx"
        if os.path.exists(idx_path):
            os.remove(idx_path)
        self.assertFalse(os.path.exists(idx_path))

        # 1. RAW UN-CHECKPOINTED RECOVERY BENCHMARK
        print("\n[BENCHMARK 1] Executing RAW UN-CHECKPOINTED log recovery (Cold Start)...")
        t0 = time.perf_counter()
        db_recovered = KVDatabase(self.db_path)
        t1 = time.perf_counter()
        raw_recovery_time = t1 - t0

        print(f"--> RAW RECOVERY TIME: {raw_recovery_time:.4f}s")
        print(f"--> Active keys recovered: {len(db_recovered.index):,}")

        self.assertEqual(len(db_recovered.index), total_keys)
        self.assertLess(raw_recovery_time, 2.0, "Recovery exceeded PDF 2.0s boundary!")

        # Clean close to write checkpoint
        db_recovered.close()
        self.assertTrue(os.path.exists(idx_path))

        # 2. CHECKPOINTED RECOVERY BENCHMARK
        print("\n[BENCHMARK 2] Executing CHECKPOINT-ACCELERATED log recovery...")
        chk_times = []
        for i in range(3):
            t2 = time.perf_counter()
            db_fast = KVDatabase(self.db_path)
            t3 = time.perf_counter()
            chk_times.append(t3 - t2)
            self.assertEqual(len(db_fast.index), total_keys)
            db_fast.close()

        best_chk = min(chk_times)
        print(f"--> CHECKPOINT RECOVERY TIME (Best of 3): {best_chk:.4f}s (Runs: {[f'{t:.4f}s' for t in chk_times]})")
        print(f"--> Active keys recovered: {total_keys:,}")
        self.assertLess(best_chk, 0.50, f"Checkpoint recovery exceeded 0.50s target: {best_chk:.4f}s")
        print("=======================================================\n")


if __name__ == "__main__":
    unittest.main()
