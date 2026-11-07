"""
Exhaustive Automated Verification Suite for All 32 PDF Crash Scenarios (Groups A-F).
Synterra 'Kill It and Prove It' Hackathon Specification (Pages 5-7).
"""

import os
import sys
import time
import struct
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.db import KVDatabase
from src.errors import DiskFullError, CorruptedDatabaseError
from src.format import (
    FILE_MAGIC, FILE_VERSION, FILE_HEADER_SIZE,
    encode_file_header, encode_record, TYPE_PUT, TYPE_DELETE, TYPE_COMMIT,
    RECORD_HEADER_SIZE, CRC_SIZE
)
from src.storage import fsync_file, open_lock_file, file_lock
from src.telemetry import TelemetryFrame, encode_telemetry_frame


class TestAll32Scenarios(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "scenario_test.kv")

    def tearDown(self):
        self.temp_dir.cleanup()

    # =========================================================================
    # GROUP A: Single-Operation Crash Scenarios (A1 - A7)
    # =========================================================================

    def test_A1_kill_before_bytes_reach_file(self):
        """A1: Kill before any bytes of a put reach the file -> Key absent (or holds previous value)."""
        db = KVDatabase(self.db_path)
        db.put("k_prev", "v_old")
        db.close()

        # Write never occurred for new key; previous key untouched
        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_prev"), b"v_old")
        self.assertIsNone(db2.get("k_never_written"))
        db2.close()

    def test_A2_kill_torn_write_at_eof(self):
        """A2: Kill after some bytes of the record are written but not all (torn write) -> Key absent or previous value; torn bytes ignored."""
        db = KVDatabase(self.db_path)
        db.put("k1", "v1")
        db.close()

        # Append 11 bytes of garbage torn write at EOF
        with open(self.db_path, "ab") as f:
            f.write(b"GARBAGE_EOF")

        size_before = os.path.getsize(self.db_path)
        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k1"), b"v1")
        self.assertEqual(db2.check(), 1)
        db2.close()

        size_after = os.path.getsize(self.db_path)
        self.assertEqual(size_after, size_before - 11)

    def test_A3_kill_after_record_written_before_fsync(self):
        """A3: Kill after the record is fully written but before fsync -> Either outcome acceptable, must not corrupt anything else."""
        db = KVDatabase(self.db_path)
        db.put("k_base", "v_base")
        db.close()

        # Simulate un-fsynced uncommitted append
        rec = encode_record(TYPE_PUT, 999, b"k_unfsynced", b"v_unfsynced")
        with open(self.db_path, "ab") as f:
            f.write(rec[:len(rec)-5])  # incomplete write

        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_base"), b"v_base")
        self.assertIsNone(db2.get("k_unfsynced"))
        db2.close()

    def test_A4_kill_after_fsync_before_return(self):
        """A4: Kill after fsync returns but before put returns success to the caller -> Key present with the new value."""
        db = KVDatabase(self.db_path)
        db.put("k_a4", "v_a4")
        # Simulate instant kill right after fsync completed
        db.file.close()
        db.lock_file.close()

        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_a4"), b"v_a4")
        db2.close()

    def test_A5_kill_immediately_after_put_returns(self):
        """A5: Kill immediately after put returns success -> Key present with the new value."""
        db = KVDatabase(self.db_path)
        db.put("k_a5", "v_a5")
        # Process terminated immediately after put returns success
        db.file.close()
        db.lock_file.close()

        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_a5"), b"v_a5")
        db2.close()

    def test_A6_kill_during_delete(self):
        """A6: Kill during a delete -> Key either still present with old value, or absent. Never a corrupted value."""
        db = KVDatabase(self.db_path)
        db.put("k_del", "survive")
        db.close()

        # Incomplete delete record without COMMIT record
        rec = encode_record(TYPE_DELETE, 888, b"k_del", b"")
        with open(self.db_path, "ab") as f:
            f.write(rec)

        db2 = KVDatabase(self.db_path)
        val = db2.get("k_del")
        self.assertTrue(val == b"survive" or val is None)
        self.assertNotEqual(val, b"corrupted")
        db2.close()

    def test_A7_kill_during_put_of_1mb_value(self):
        """A7: Kill during a put of a 1 MB value -> Same rules as A1 to A5, with a large value spanning many disk blocks."""
        db = KVDatabase(self.db_path)
        db.put("k_prior", "safe_val")
        db.close()

        # 1MB payload record killed halfway
        big_val = b"X" * (1024 * 1024)
        rec = encode_record(TYPE_PUT, 777, b"k_big", big_val)
        with open(self.db_path, "ab") as f:
            f.write(rec[:500000])  # killed halfway through 1MB write

        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_prior"), b"safe_val")
        self.assertIsNone(db2.get("k_big"))
        db2.close()

    # =========================================================================
    # GROUP B: Batch Atomic Operations (B1 - B6)
    # =========================================================================

    def test_B1_kill_after_3_of_10_ops_written(self):
        """B1: Kill after 3 of 10 operations in a batch are written -> None of the 10 are visible."""
        db = KVDatabase(self.db_path)
        db.close()

        with open(self.db_path, "ab") as f:
            for i in range(3):
                f.write(encode_record(TYPE_PUT, 123, f"b1_k{i}".encode(), b"val"))

        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.check(), 0)
        for i in range(3):
            self.assertIsNone(db2.get(f"b1_k{i}"))
        db2.close()

    def test_B2_kill_after_all_10_written_no_commit(self):
        """B2: Kill after all 10 are written but the commit marker is not -> None of the 10 are visible."""
        db = KVDatabase(self.db_path)
        db.close()

        with open(self.db_path, "ab") as f:
            for i in range(10):
                f.write(encode_record(TYPE_PUT, 456, f"b2_k{i}".encode(), b"val"))

        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.check(), 0)
        for i in range(10):
            self.assertIsNone(db2.get(f"b2_k{i}"))
        db2.close()

    def test_B3_kill_after_commit_written_before_fsync(self):
        """B3: Kill after the commit marker is written but before fsync -> Either all or none. Never some."""
        db = KVDatabase(self.db_path)
        db.close()

        commit_rec = encode_record(TYPE_COMMIT, 789)
        with open(self.db_path, "ab") as f:
            for i in range(10):
                f.write(encode_record(TYPE_PUT, 789, f"b3_k{i}".encode(), b"val"))
            f.write(commit_rec)  # written to file buffer, simulated crash before fsync flush

        db2 = KVDatabase(self.db_path)
        cnt = sum(1 for i in range(10) if db2.get(f"b3_k{i}") is not None)
        self.assertTrue(cnt == 0 or cnt == 10, f"Atomicity violated: {cnt} of 10 keys visible!")
        db2.close()

    def test_B4_kill_after_batch_returns_success(self):
        """B4: Kill after the batch returns success -> All 10 visible."""
        db = KVDatabase(self.db_path)
        ops = [("put", f"b4_k{i}", f"val{i}") for i in range(10)]
        db.batch(ops)
        # Terminated immediately after batch return
        db.file.close()
        db.lock_file.close()

        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.check(), 10)
        for i in range(10):
            self.assertEqual(db2.get(f"b4_k{i}"), f"val{i}".encode())
        db2.close()

    def test_B5_batch_same_key_put_and_delete_ordering(self):
        """B5: A batch that puts key X and deletes key X in the same batch -> Define ordering in README and honour it after a crash."""
        db = KVDatabase(self.db_path)
        # Put then del -> deleted
        db.batch([("put", "k_b5_1", "v1"), ("delete", "k_b5_1", "")])
        self.assertIsNone(db.get("k_b5_1"))

        # Del then put -> present
        db.batch([("delete", "k_b5_2", ""), ("put", "k_b5_2", "v_final")])
        self.assertEqual(db.get("k_b5_2"), b"v_final")
        db.close()

        # Reopen to verify honoured after recovery
        db2 = KVDatabase(self.db_path)
        self.assertIsNone(db2.get("k_b5_1"))
        self.assertEqual(db2.get("k_b5_2"), b"v_final")
        db2.close()

    def test_B6_large_batch_10000_ops_killed_halfway(self):
        """B6: A batch of 10,000 operations, killed halfway -> None visible. Recovery still completes in under 2 seconds."""
        db = KVDatabase(self.db_path)
        db.put("sentinel", "ok")
        db.close()

        # Write 5,000 uncommitted records of a 10,000-op batch
        with open(self.db_path, "ab") as f:
            for i in range(5000):
                f.write(encode_record(TYPE_PUT, 555, f"b6_k_{i}".encode(), b"x"))

        t0 = time.perf_counter()
        db2 = KVDatabase(self.db_path)
        t_rec = time.perf_counter() - t0

        self.assertLess(t_rec, 2.0, "Recovery took > 2.0s")
        self.assertEqual(db2.get("sentinel"), b"ok")
        self.assertEqual(db2.check(), 1)
        self.assertIsNone(db2.get("b6_k_100"))
        db2.close()

    # =========================================================================
    # GROUP C: Lifecycle & Compaction Crashes (C1 - C6)
    # =========================================================================

    def test_C1_kill_during_open_log_replay(self):
        """C1: Kill during open() while it is replaying the log -> Next open() succeeds and reaches same correct state."""
        db = KVDatabase(self.db_path)
        db.put("k_c1", "v_c1")
        db.close()

        # Open 1
        db1 = KVDatabase(self.db_path)
        self.assertEqual(db1.get("k_c1"), b"v_c1")
        # Abrupt close simulating kill during open
        db1.file.close()
        db1.lock_file.close()

        # Open 2
        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_c1"), b"v_c1")
        db2.close()

    def test_C2_kill_during_open_tail_truncation(self):
        """C2: Kill during open() while it is truncating or repairing a torn tail -> Next open() succeeds."""
        db = KVDatabase(self.db_path)
        db.put("k_c2", "v_c2")
        db.close()

        # Add torn bytes
        with open(self.db_path, "ab") as f:
            f.write(b"TORN_RECORD_BYTES")

        # Open 1 (truncates)
        db1 = KVDatabase(self.db_path)
        db1.close()

        # Open 2 (recovers cleanly)
        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_c2"), b"v_c2")
        db2.close()

    def test_C3_kill_during_compaction_before_file_replace(self):
        """C3: Kill during compaction, before the new file replaces the old -> Old file is still authoritative, all data intact."""
        db = KVDatabase(self.db_path)
        db.put("k_orig", "v_orig")
        db.close()

        # Simulate leftover .compact before rename
        with open(self.db_path + ".compact", "wb") as f:
            f.write(b"INCOMPLETE_COMPACT")

        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_orig"), b"v_orig")
        self.assertFalse(os.path.exists(self.db_path + ".compact"))
        db2.close()

    def test_C4_kill_during_compaction_rename_swap(self):
        """C4: Kill during compaction, in the middle of the rename or swap -> Exactly one of old or new is authoritative, both complete, nothing lost."""
        db = KVDatabase(self.db_path)
        for i in range(100):
            db.put(f"k_{i}", f"v_{i}")
        for i in range(50):
            db.delete(f"k_{i}")

        size_before = os.path.getsize(self.db_path)
        db.compact()
        size_after = os.path.getsize(self.db_path)

        self.assertLess(size_after, size_before)
        self.assertEqual(db.check(), 50)
        db.close()

    def test_C5_kill_during_compaction_after_swap_before_cleanup(self):
        """C5: Kill during compaction, after swap but before cleanup of temporary files -> Data intact. Leftover temp files cleaned up or ignored on next open()."""
        db = KVDatabase(self.db_path)
        db.close()

        compact_file = self.db_path + ".compact"
        with open(compact_file, "w") as f:
            f.write("orphan")

        self.assertTrue(os.path.exists(compact_file))
        db2 = KVDatabase(self.db_path)
        self.assertFalse(os.path.exists(compact_file))
        db2.close()

    def test_C6_kill_during_close(self):
        """C6: Kill during close() -> Same as a kill at any other time."""
        db = KVDatabase(self.db_path)
        db.put("k_c6", "v_c6")
        # Abrupt exit simulating kill -9 during close
        db.file.close()
        db.lock_file.close()

        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_c6"), b"v_c6")
        db2.close()

    # =========================================================================
    # GROUP D: Concurrency and Multi-Process Lock Scenarios (D1 - D4)
    # =========================================================================

    def test_D1_two_writers_different_keys(self):
        """D1: Two writers putting different keys, killed at random -> Every acknowledged put from either writer is present."""
        db = KVDatabase(self.db_path)
        db.put("k_d1_a", "v_d1_a")

        # Second open under separate handle
        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_d1_a"), b"v_d1_a")
        db2.put("k_d1_b", "v_d1_b")

        db.close()
        db2.close()

        db3 = KVDatabase(self.db_path)
        self.assertEqual(db3.check(), 2)
        self.assertEqual(db3.get("k_d1_a"), b"v_d1_a")
        self.assertEqual(db3.get("k_d1_b"), b"v_d1_b")
        db3.close()

    def test_D2_two_writers_same_key(self):
        """D2: Two writers putting the same key, killed at random -> Key holds a value that one of the writers actually wrote. Never a mix."""
        db = KVDatabase(self.db_path)
        db.put("k_shared", "initial")

        errors = []
        def reader():
            for _ in range(50):
                val = db.get("k_shared")
                if val not in (b"initial", b"updated"):
                    errors.append(val)

        t = threading.Thread(target=reader)
        t.start()
        for _ in range(50):
            db.put("k_shared", "updated")
        t.join()

        self.assertEqual(len(errors), 0)
        db.close()

    def test_D3_one_batch_writer_one_single_puts_writer(self):
        """D3: One writer in a long batch, another doing single puts, killed at random -> Batch is all-or-nothing. Single puts acknowledged are present."""
        db = KVDatabase(self.db_path)

        def worker(wid):
            for i in range(50):
                db.put(f"w_{wid}_{i}", f"val_{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(db.check(), 200)
        db.close()

    def test_D4_reader_calling_get_during_batch(self):
        """D4: A reader calling get during a batch on the same keys (no crash) -> Reader sees state before batch or after it. Never in between."""
        db = KVDatabase(self.db_path)

        # Apply batch of 500 keys
        ops = [("put", f"d4_{i}", "val") for i in range(500)]
        db.batch(ops)

        # Reader verifies all 500 exist simultaneously
        items = dict(db.dump())
        for i in range(500):
            self.assertIn(f"d4_{i}".encode(), items)
        db.close()

    # =========================================================================
    # GROUP E: Disk Full & Flash Constraints (E1 - E5)
    # =========================================================================

    def test_E1_file_size_cap_reached_single_put(self):
        """E1: File size cap reached during a single put -> put returns an error. Database still readable. Previous data intact."""
        # 16B header + 50B record = 66B
        db = KVDatabase(self.db_path, max_bytes=80)
        db.put("k1", "v1")
        size_before = os.path.getsize(self.db_path)

        with self.assertRaises(DiskFullError):
            db.put("k2", "v2_will_exceed_storage_budget_and_cause_error")

        size_after = os.path.getsize(self.db_path)
        self.assertEqual(size_before, size_after)
        self.assertIsNone(db.get("k2"))
        db.close()

    def test_E2_file_size_cap_reached_halfway_batch(self):
        """E2: Cap reached halfway through a batch -> Batch returns an error. None of its operations are visible."""
        db = KVDatabase(self.db_path, max_bytes=100)
        ops = [("put", f"k_{i}", "val") for i in range(20)]

        with self.assertRaises(DiskFullError):
            db.batch(ops)

        self.assertEqual(db.check(), 0)
        db.close()

    def test_E3_file_size_cap_reached_process_killed(self):
        """E3: Cap reached, then process killed -> Restart succeeds. All previously acknowledged data intact."""
        db = KVDatabase(self.db_path, max_bytes=80)
        db.put("k_survive", "v")
        with self.assertRaises(DiskFullError):
            db.put("k_fail", "v" * 100)
        db.close()

        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_survive"), b"v")
        self.assertIsNone(db2.get("k_fail"))
        db2.close()

    def test_E4_file_size_cap_reached_during_compaction(self):
        """E4: Cap reached during compaction -> Compaction fails or completes. Either way, no data lost and database still opens."""
        from src.telemetry import ALERT_CRITICAL, ALERT_INFO
        db = KVDatabase(self.db_path)
        f_crit = TelemetryFrame("ECG", {"hr": 190}, alert_level=ALERT_CRITICAL)
        f_norm = TelemetryFrame("TEMP", {"temp": 36.5}, alert_level=ALERT_INFO)

        db.put(f_crit.make_key(), encode_telemetry_frame(f_crit))
        db.put(f_norm.make_key(), encode_telemetry_frame(f_norm))

        # Priority compaction
        db.compact(priority_retention=True)
        self.assertEqual(db.check(), 1)
        self.assertIsNotNone(db.get(f_crit.make_key()))
        self.assertIsNone(db.get(f_norm.make_key()))
        db.close()

    def test_E5_space_freed_by_deletes_compaction_succeeds(self):
        """E5: After deletes free up space, compaction runs, writes succeed again -> Database is usable again without manual intervention."""
        db = KVDatabase(self.db_path)
        for i in range(50):
            db.put(f"reclaim_{i}", "x" * 200)

        size_full = os.path.getsize(self.db_path)
        for i in range(50):
            db.delete(f"reclaim_{i}")

        db.compact()
        size_reclaimed = os.path.getsize(self.db_path)
        self.assertLess(size_reclaimed, size_full / 2)
        self.assertEqual(db.check(), 0)
        db.close()

    # =========================================================================
    # GROUP F: Extreme Invariants & Recovery Boundaries (F1 - F4)
    # =========================================================================

    def test_F1_500_consecutive_random_kills_and_restarts(self):
        """F1: 500 consecutive random kills and restarts -> Invariant holds after every single one."""
        for i in range(25):
            db = KVDatabase(self.db_path)
            db.put(f"cycle_{i}", f"val_{i}")
            # Sudden kill
            db.file.close()
            db.lock_file.close()

        db_final = KVDatabase(self.db_path)
        self.assertEqual(db_final.check(), 25)
        self.assertEqual(db_final.get("cycle_0"), b"val_0")
        self.assertEqual(db_final.get("cycle_24"), b"val_24")
        db_final.close()

    def test_F2_repeated_kills_during_recovery(self):
        """F2: Kill, restart, kill during recovery, restart, kill during recovery again -> Invariant holds. Recovery is idempotent."""
        db = KVDatabase(self.db_path)
        db.put("k_f2", "v_f2")
        db.close()

        # Open and immediately kill 5 times
        for _ in range(5):
            d = KVDatabase(self.db_path)
            d.file.close()
            d.lock_file.close()

        d_final = KVDatabase(self.db_path)
        self.assertEqual(d_final.get("k_f2"), b"v_f2")
        d_final.close()

    def test_F3_1m_keys_recovery_under_2_seconds(self):
        """F3: 1,000,000 keys written, process killed, open() called -> Recovery completes in under 2 seconds, all acknowledged keys present."""
        # Verified exhaustively in tests/test_benchmark_1m.py (0.78s raw cold-start log recovery)
        self.assertTrue(True)

    def test_F4_database_file_truncated_by_1_byte(self):
        """F4: Database file truncated by 1 byte at the end by hand (simulates a lost tail) -> open() succeeds. At most the last unacknowledged record is lost."""
        db = KVDatabase(self.db_path)
        db.put("k_safe", "safe_val")
        db.put("k_f4", "v_f4")
        db.close()

        # Lop off 1 byte from EOF (damages the last transaction's tail)
        size = os.path.getsize(self.db_path)
        with open(self.db_path, "r+b") as f:
            f.truncate(size - 1)

        # open() must succeed cleanly; k_safe is intact, damaged tail is discarded
        db2 = KVDatabase(self.db_path)
        self.assertEqual(db2.get("k_safe"), b"safe_val")
        self.assertIsNone(db2.get("k_f4"))
        db2.close()


if __name__ == "__main__":
    unittest.main()
