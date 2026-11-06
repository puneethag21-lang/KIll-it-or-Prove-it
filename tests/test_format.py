"""
Unit tests for binary format encoding, decoding, and crash-resilience.
"""

import unittest
from src.format import (
    FILE_MAGIC, FILE_VERSION, FILE_HEADER_SIZE,
    encode_file_header, is_valid_file_header,
    encode_record, decode_record_from_bytes,
    TYPE_PUT, TYPE_DELETE, TYPE_COMMIT,
    MIN_KEY_LEN, MAX_KEY_LEN, MAX_VALUE_LEN,
    RECORD_HEADER_SIZE, CRC_SIZE
)


class TestFormat(unittest.TestCase):

    def test_file_header(self):
        hdr = encode_file_header()
        self.assertEqual(len(hdr), FILE_HEADER_SIZE)
        self.assertTrue(is_valid_file_header(hdr))
        self.assertFalse(is_valid_file_header(hdr[:10]))  # truncated
        self.assertFalse(is_valid_file_header(b"BADMAGIC" + hdr[8:]))  # bad magic
        self.assertFalse(is_valid_file_header(hdr[:8] + b"\x02\x00\x00\x00" + hdr[12:]))  # bad version

    def test_encode_decode_put(self):
        key = b"user_123"
        val = b"alice_data"
        txid = 987654321
        rec = encode_record(TYPE_PUT, txid, key, val)
        
        expected_len = RECORD_HEADER_SIZE + len(key) + len(val) + CRC_SIZE
        self.assertEqual(len(rec), expected_len)

        decoded = decode_record_from_bytes(rec)
        self.assertIsNotNone(decoded)
        rec_type, d_txid, d_key, d_val, rec_len = decoded
        self.assertEqual(rec_type, TYPE_PUT)
        self.assertEqual(d_txid, txid)
        self.assertEqual(d_key, key)
        self.assertEqual(d_val, val)
        self.assertEqual(rec_len, expected_len)

    def test_encode_decode_delete(self):
        key = b"session_token"
        txid = 42
        rec = encode_record(TYPE_DELETE, txid, key)
        decoded = decode_record_from_bytes(rec)
        self.assertIsNotNone(decoded)
        rec_type, d_txid, d_key, d_val, _ = decoded
        self.assertEqual(rec_type, TYPE_DELETE)
        self.assertEqual(d_txid, txid)
        self.assertEqual(d_key, key)
        self.assertEqual(d_val, b"")

    def test_encode_decode_commit(self):
        txid = 42
        rec = encode_record(TYPE_COMMIT, txid)
        self.assertEqual(len(rec), 23)  # 19 header + 4 CRC
        decoded = decode_record_from_bytes(rec)
        self.assertIsNotNone(decoded)
        rec_type, d_txid, d_key, d_val, _ = decoded
        self.assertEqual(rec_type, TYPE_COMMIT)
        self.assertEqual(d_txid, txid)
        self.assertEqual(d_key, b"")
        self.assertEqual(d_val, b"")

    def test_torn_bytes_return_none_without_exception(self):
        """Invariant: decode must NEVER raise on bad bytes."""
        rec = encode_record(TYPE_PUT, 1, b"key", b"value")
        for i in range(len(rec)):
            torn = rec[:i]
            self.assertIsNone(decode_record_from_bytes(torn), f"Failed for length {i}")

    def test_corrupt_crc_returns_none(self):
        rec = bytearray(encode_record(TYPE_PUT, 1, b"key", b"value"))
        rec[-1] ^= 0xFF  # Corrupt CRC byte
        self.assertIsNone(decode_record_from_bytes(bytes(rec)))

    def test_corrupt_payload_returns_none(self):
        rec = bytearray(encode_record(TYPE_PUT, 1, b"key", b"value"))
        rec[RECORD_HEADER_SIZE + 1] ^= 0xFF  # Flip bit in key
        self.assertIsNone(decode_record_from_bytes(bytes(rec)))

    def test_invalid_types_and_lengths_return_none(self):
        # Invalid type
        rec = bytearray(encode_record(TYPE_PUT, 1, b"key", b"value"))
        rec[4] = 99  # Invalid record type
        self.assertIsNone(decode_record_from_bytes(bytes(rec)))

        # Commit with payload
        bad_commit = encode_record(TYPE_COMMIT, 1, b"extra", b"")
        self.assertIsNone(decode_record_from_bytes(bad_commit))

        # Delete with value
        bad_del = encode_record(TYPE_DELETE, 1, b"key", b"val")
        self.assertIsNone(decode_record_from_bytes(bad_del))


if __name__ == "__main__":
    unittest.main()
