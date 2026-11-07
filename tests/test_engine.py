"""
Comprehensive test suite for KVDatabase engine covering failure and robustness scenarios.
"""

import os
import unittest
import tempfile
from src.db import KVDatabase
from src.errors import DiskFullError
from src.format import (
    TYPE_PUT, TYPE_COMMIT, encode_record, FILE_HEADER_SIZE
)
from src.storage import fsync_file


class TestKVDatabaseEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.kv")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_basic_crud(self):
        db = KVDatabase(self.db_path)
        try:
            # Put
            db.put(b"k1", b"v1")
            db.put(b"k2", b"v2")
            self.assertEqual(db.get(b"k1"), b"v1")
            self.assertEqual(db.get(b"k2"), b"v2")
            self.assertEqual(db.check(), 2)

            # Overwrite
            db.put(b"k1", b"v1_updated")
            self.assertEqual(db.get(b"k1"), b"v1_updated")

            # Delete
            db.delete(b"k2")
            self.assertIsNone(db.get(b"k2"))
            self.assertEqual(db.check(), 1)

            # Delete non-existent key (not an error)
            db.delete(b"non_existent")
        finally:
            db.close()

    def test_batch_ordering_last_op_wins(self):
        """Scenario B5: put key X and delete key X in the same batch."""
        db = KVDatabase(self.db_path)
        try:
            # Batch 1: put then del -> should end up deleted
            db.batch([
                ("put", b"x", b"val1"),
                ("del", b"x", b"")
            ])
            self.assertIsNone(db.get(b"x"))

            # Batch 2: del then put -> should end up with val2
            db.batch([
                ("del", b"x", b""),
                ("put", b"x", b"val2")
            ])
            self.assertEqual(db.get(b"x"), b"val2")
        finally:
            db.close()

    def test_uncommitted_batch_ignored_and_truncated(self):
        """Scenarios B1, B2: uncommitted records must be invisible and truncated."""
        db = KVDatabase(self.db_path)
        db.put(b"committed_key", b"committed_val")
        db.close()

        # Manually append an uncommitted PUT record directly to disk
        txid = 999
        uncommitted_rec = encode_record(TYPE_PUT, txid, b"ghost_key", b"ghost_val")
        with open(self.db_path, "a+b") as f:
            f.write(uncommitted_rec)
            fsync_file(f)

        # Reopen DB
        db2 = KVDatabase(self.db_path)
        try:
            self.assertIsNone(db2.get(b"ghost_key"))
            self.assertEqual(db2.get(b"committed_key"), b"committed_val")
            self.assertEqual(db2.check(), 1)
        finally:
            db2.close()

        # Verify the file was truncated back to the last commit
        expected_size = FILE_HEADER_SIZE + len(encode_record(TYPE_PUT, 1, b"committed_key", b"committed_val")) + 23
        # Size without ghost_rec
        self.assertLess(os.path.getsize(self.db_path), expected_size + len(uncommitted_rec))

    def test_torn_tail_recovery(self):
        """Scenario A2, F4: file truncated by hand or torn bytes at EOF."""
        db = KVDatabase(self.db_path)
        db.put(b"stable", b"value")
        db.close()

        # Append corrupted / torn bytes
        with open(self.db_path, "a+b") as f:
            f.write(b"KVDB_TORN_BYTES_RANDOM_NOISE_123456789")
            fsync_file(f)

        db2 = KVDatabase(self.db_path)
        try:
            self.assertEqual(db2.get(b"stable"), b"value")
            self.assertEqual(db2.check(), 1)
        finally:
            db2.close()

    def test_disk_full_max_bytes(self):
        """Scenarios E1, E2: max_bytes enforcement."""
        # 16 bytes header + record size
        limit = FILE_HEADER_SIZE + 100
        db = KVDatabase(self.db_path, max_bytes=limit)
        try:
            db.put(b"small_k", b"v")  # Fits in 100 bytes
            self.assertEqual(db.get(b"small_k"), b"v")

            # Large write exceeding max_bytes
            with self.assertRaises(DiskFullError):
                db.put(b"overflow_k", b"A" * 200)

            # Database must remain fully readable and intact
            self.assertEqual(db.get(b"small_k"), b"v")
        finally:
            db.close()

        # Reopening must also succeed and data intact
        db2 = KVDatabase(self.db_path, max_bytes=limit)
        try:
            self.assertEqual(db2.get(b"small_k"), b"v")
        finally:
            db2.close()

    def test_compaction(self):
        """Compaction shrinks file and preserves active keys."""
        db = KVDatabase(self.db_path)
        try:
            # Write 50 updates to same key
            for i in range(50):
                db.put(b"churn_key", f"churn_val_{i}".encode())
            db.put(b"delete_me", b"to_be_deleted")
            db.delete(b"delete_me")
            db.put(b"keep_me", b"permanent_val")

            size_before = os.path.getsize(self.db_path)

            db.compact()

            size_after = os.path.getsize(self.db_path)
            self.assertLess(size_after, size_before)
            self.assertEqual(db.get(b"churn_key"), b"churn_val_49")
            self.assertEqual(db.get(b"keep_me"), b"permanent_val")
            self.assertIsNone(db.get(b"delete_me"))
            self.assertEqual(db.check(), 2)
        finally:
            db.close()

    def test_dump_sorted(self):
        db = KVDatabase(self.db_path)
        try:
            db.put(b"banana", b"1")
            db.put(b"apple", b"2")
            db.put(b"cherry", b"3")

            dumped = db.dump()
            keys = [k for k, _ in dumped]
            self.assertEqual(keys, [b"apple", b"banana", b"cherry"])
        finally:
            db.close()

    def test_telemetry_and_priority_compaction(self):
        """Test telemetry frame encoding/decoding and priority retention under flash constraint."""
        from src.telemetry import TelemetryFrame, encode_telemetry_frame, decode_telemetry_frame, ALERT_CRITICAL, ALERT_INFO

        db = KVDatabase(self.db_path)
        try:
            f_norm = TelemetryFrame("GPS-01", {"lat": 37.77, "lon": -122.41}, alert_level=ALERT_INFO)
            f_crit = TelemetryFrame("ECG-01", {"heart_rate": 180, "arrhythmia": True}, alert_level=ALERT_CRITICAL)

            db.put(f_norm.make_key(), encode_telemetry_frame(f_norm))
            db.put(f_crit.make_key(), encode_telemetry_frame(f_crit))

            self.assertEqual(db.check(), 2)

            # Verify decode
            raw_crit = db.get(f_crit.make_key())
            decoded_crit = decode_telemetry_frame(raw_crit)
            self.assertEqual(decoded_crit.sensor_id, "ECG-01")
            self.assertTrue(decoded_crit.is_critical)
            self.assertEqual(decoded_crit.metrics["heart_rate"], 180)

            # Priority compaction prunes non-critical while preserving crit:
            db.compact(priority_retention=True)
            self.assertEqual(db.check(), 1)
            self.assertIsNotNone(db.get(f_crit.make_key()))
            self.assertIsNone(db.get(f_norm.make_key()))
        finally:
            db.close()

    def test_audit_hash_attestation(self):
        """Test cryptographic hash chain attestation in check()."""
        db = KVDatabase(self.db_path)
        try:
            db.put(b"k1", b"v1")
            count, hash1 = db.check(return_hash=True)
            self.assertEqual(count, 1)
            self.assertEqual(len(hash1), 64)

            # Adding another key deterministically mutates hash
            db.put(b"k2", b"v2")
            count2, hash2 = db.check(return_hash=True)
            self.assertEqual(count2, 2)
            self.assertNotEqual(hash1, hash2)
        finally:
            db.close()

    def test_stats_observability(self):
        """Test stats() output with live keys, tombstones, and compaction reclamation."""
        db = KVDatabase(self.db_path)
        try:
            db.put("k1", "v1")
            db.put("k2", "v2")
            db.delete("k1")
            s = db.stats()
            self.assertEqual(s["live_keys"], 1)
            self.assertEqual(s["tombstones"], 1)
            self.assertGreater(s["reclaimable_bytes"], 0)

            db.compact()
            s_compact = db.stats()
            self.assertEqual(s_compact["live_keys"], 1)
            self.assertEqual(s_compact["tombstones"], 0)
            self.assertEqual(s_compact["reclaimable_bytes"], 0)
        finally:
            db.close()

    def test_edge_cases_sub_header_and_empty_values(self):
        """Test recovery on <16 byte files and zero-length values."""
        # 1. File smaller than 16-byte header
        with open(self.db_path, "wb") as f:
            f.write(b"SHORT")
        db = KVDatabase(self.db_path)
        self.assertEqual(db.check(), 0)
        self.assertEqual(os.path.getsize(self.db_path), 16)

        # 2. Zero-length value is allowed
        db.put("empty_val_key", "")
        self.assertEqual(db.get("empty_val_key"), b"")

        # 3. Zero-length key raises ValueError
        with self.assertRaises(ValueError):
            db.put("", "some_val")
        with self.assertRaises(ValueError):
            db.delete("")
        db.close()


if __name__ == "__main__":
    unittest.main()

