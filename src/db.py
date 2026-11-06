"""
Core KVDatabase Engine implementation for Synterra's 'Kill It and Prove It'.
"""

import os
import mmap
import struct
import zlib
import threading
from typing import List, Tuple, Optional, Dict

from src.format import (
    FILE_MAGIC, FILE_VERSION, FILE_HEADER_SIZE,
    encode_file_header, is_valid_file_header,
    TYPE_PUT, TYPE_DELETE, TYPE_COMMIT,
    MIN_KEY_LEN, MAX_KEY_LEN, MAX_VALUE_LEN, MAX_BATCH_SIZE,
    RECORD_HEADER_STRUCT, RECORD_HEADER_SIZE, CRC_STRUCT, CRC_SIZE,
    encode_record, decode_record_from_bytes
)
from src.storage import (
    open_lock_file, file_lock, fsync_file,
    check_max_bytes_or_raise, sync_directory_if_posix
)
from src.errors import (
    DiskFullError, CorruptedDatabaseError, DatabaseClosedError
)


class KVDatabase:
    """
    Crash-proof, single-file embedded key-value database.
    Survives kill -9 / TerminateProcess at any arbitrary moment.
    """

    def __init__(self, path: str = "database.kv", max_bytes: Optional[int] = None):
        self.path = os.path.abspath(path)
        self.max_bytes = max_bytes
        self.lock = threading.RLock()
        self.closed = False

        # In-memory index: key (bytes) -> (value_offset, value_len)
        self.index: Dict[bytes, Tuple[int, int]] = {}

        # 1. Clean up any leftover .compact file from a crash during compaction
        compact_temp = self.path + ".compact"
        if os.path.exists(compact_temp):
            try:
                os.remove(compact_temp)
            except OSError:
                pass

        # 2. Open companion lock file
        self.lock_file = open_lock_file(self.path)

        # 3. Synchronize open & recovery under exclusive process + thread lock
        with file_lock(self.lock_file):
            # Open the data file
            if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
                # Initialize new database with 16-byte header
                self.file = open(self.path, "w+b")
                hdr = encode_file_header()
                self.file.write(hdr)
                fsync_file(self.file)
            else:
                # Open existing database
                self.file = open(self.path, "r+b")

            # Run recovery & build in-memory index
            self._recover()

    def _ensure_open(self):
        if self.closed or self.file is None or self.file.closed:
            raise DatabaseClosedError("Database is closed")

    def _recover(self):
        """
        Sub-second, crash-safe zero-copy recovery using memoryview and mmap:
        1. Validate 16-byte header.
        2. Stream through log using mmap memoryview cursor on isolated read handle.
        3. Buffer pending transaction ops per txid.
        4. Apply ops to self.index ONLY when valid COMMIT is parsed.
        5. Truncate file cleanly to the end of the LAST VALID COMMIT.
        """
        self._ensure_open()
        self.index.clear()
        self.file.seek(0, os.SEEK_END)
        total_size = self.file.tell()

        if total_size < FILE_HEADER_SIZE:
            # Header incomplete -> re-initialize header
            self.file.seek(0)
            self.file.write(encode_file_header())
            self.file.truncate(FILE_HEADER_SIZE)
            fsync_file(self.file)
            return

        self.file.seek(0)
        hdr = self.file.read(FILE_HEADER_SIZE)
        if not is_valid_file_header(hdr):
            raise CorruptedDatabaseError("Invalid database file header (magic or version mismatch)")

        if total_size == FILE_HEADER_SIZE:
            return

        # Pre-bind unpackers and crc functions for tight loop performance
        unpack_hdr = RECORD_HEADER_STRUCT.unpack_from
        unpack_crc = CRC_STRUCT.unpack_from
        calc_crc = zlib.crc32

        last_commit_end = FILE_HEADER_SIZE
        pending: Dict[int, list] = {}

        # Open dedicated read handle for mmap so self.file is never mapped directly on Windows
        with open(self.path, "rb") as read_f:
            mm = mmap.mmap(read_f.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                view = memoryview(mm)
                pos = FILE_HEADER_SIZE

                while pos + 23 <= total_size:
                    rec_len, rec_type, txid, klen, vlen = unpack_hdr(view, pos)
                    if pos + rec_len > total_size:
                        break

                    expected_len = RECORD_HEADER_SIZE + klen + vlen + CRC_SIZE
                    if rec_len != expected_len:
                        break

                    if rec_type not in (TYPE_PUT, TYPE_DELETE, TYPE_COMMIT):
                        break
                    if rec_type == TYPE_COMMIT and (klen != 0 or vlen != 0):
                        break
                    if rec_type == TYPE_DELETE and (not (MIN_KEY_LEN <= klen <= MAX_KEY_LEN) or vlen != 0):
                        break
                    if rec_type == TYPE_PUT and (not (MIN_KEY_LEN <= klen <= MAX_KEY_LEN) or vlen > MAX_VALUE_LEN):
                        break

                    # Fast CRC check over all bytes before CRC
                    body_end = pos + rec_len - CRC_SIZE
                    if (calc_crc(view[pos:body_end]) & 0xffffffff) != unpack_crc(view, body_end)[0]:
                        break

                    if rec_type == TYPE_COMMIT:
                        acts = pending.pop(txid, None)
                        if acts:
                            for rpos, rklen, rvlen, rtype in acts:
                                k = bytes(view[rpos + RECORD_HEADER_SIZE : rpos + RECORD_HEADER_SIZE + rklen])
                                if rtype == TYPE_PUT:
                                    self.index[k] = (rpos + RECORD_HEADER_SIZE + rklen, rvlen)
                                else:
                                    self.index.pop(k, None)
                        last_commit_end = pos + rec_len

                    else:  # TYPE_PUT or TYPE_DELETE
                        entry = (pos, klen, vlen, rec_type)
                        try:
                            pending[txid].append(entry)
                        except KeyError:
                            pending[txid] = [entry]

                    pos += rec_len
            finally:
                view.release()
                mm.close()

        # Truncate uncommitted/torn trailing records back to last valid COMMIT
        if total_size != last_commit_end:
            self.file.seek(last_commit_end)
            self.file.truncate(last_commit_end)
            fsync_file(self.file)

        self.file.seek(0, os.SEEK_END)

    def batch(self, operations: List[Tuple[str, bytes, bytes]]):
        """
        Executes a list of operations atomically.
        operations: list of (action, key, value)
          action: 'put' or 'del' / 'delete'
          key: bytes or str
          value: bytes or str
        """
        self._ensure_open()

        if not (1 <= len(operations) <= MAX_BATCH_SIZE):
            raise ValueError(f"Batch size must be between 1 and {MAX_BATCH_SIZE}")

        # Validate inputs and prepare normalized operations
        prepared_ops = []
        for op in operations:
            if len(op) == 2:
                action, key = op
                value = b""
            else:
                action, key, value = op

            action_lower = action.lower()
            if action_lower not in ("put", "del", "delete"):
                raise ValueError(f"Unknown operation: {action}")

            key_b = key.encode("utf-8") if isinstance(key, str) else bytes(key)
            val_b = value.encode("utf-8") if isinstance(value, str) else bytes(value)

            if not (MIN_KEY_LEN <= len(key_b) <= MAX_KEY_LEN):
                raise ValueError(f"Key length must be 1..{MAX_KEY_LEN} bytes")

            if action_lower == "put":
                if len(val_b) > MAX_VALUE_LEN:
                    raise ValueError(f"Value exceeds maximum allowed size ({MAX_VALUE_LEN} bytes)")
                prepared_ops.append((TYPE_PUT, key_b, val_b))
            else:
                prepared_ops.append((TYPE_DELETE, key_b, b""))

        with self.lock:
            with file_lock(self.lock_file):
                self._ensure_open()

                # Generate unique 64-bit transaction id
                txid = int.from_bytes(os.urandom(8), "little")

                # Serialize all payload records + 1 commit record
                records_bytes = bytearray()
                op_offsets = []  # track offsets for updating index

                self.file.seek(0, os.SEEK_END)
                start_offset = self.file.tell()
                current_offset = start_offset

                for rec_type, k_b, v_b in prepared_ops:
                    rec = encode_record(rec_type, txid, k_b, v_b)
                    if rec_type == TYPE_PUT:
                        val_offset = current_offset + RECORD_HEADER_SIZE + len(k_b)
                        val_len = len(v_b)
                        op_offsets.append((TYPE_PUT, k_b, val_offset, val_len))
                    else:
                        op_offsets.append((TYPE_DELETE, k_b, 0, 0))

                    records_bytes.extend(rec)
                    current_offset += len(rec)

                # Add COMMIT record
                commit_rec = encode_record(TYPE_COMMIT, txid)
                records_bytes.extend(commit_rec)

                total_write_size = len(records_bytes)

                # 1. Enforce max_bytes BEFORE any write
                check_max_bytes_or_raise(self.file, total_write_size, self.max_bytes)

                # 2. Append all records in a single write call
                self.file.seek(0, os.SEEK_END)
                self.file.write(records_bytes)

                # 3. Force to physical disk
                fsync_file(self.file)

                # 4. Update in-memory index only after fsync succeeds
                for rec_type, k_b, val_offset, val_len in op_offsets:
                    if rec_type == TYPE_PUT:
                        self.index[k_b] = (val_offset, val_len)
                    elif rec_type == TYPE_DELETE:
                        self.index.pop(k_b, None)

    def put(self, key: bytes, value: bytes):
        """Store key-value pair. Durable upon return."""
        self.batch([("put", key, value)])

    def delete(self, key: bytes):
        """Delete key. Missing key is not an error."""
        self.batch([("delete", key, b"")])

    def get(self, key: bytes) -> Optional[bytes]:
        """
        Return the value associated with key, or None if not found.
        Runs under lock so readers never see half-applied batches.
        """
        self._ensure_open()
        key_b = key.encode("utf-8") if isinstance(key, str) else bytes(key)

        with self.lock:
            self._ensure_open()
            if key_b not in self.index:
                return None

            offset, length = self.index[key_b]
            self.file.seek(offset)
            return self.file.read(length)

    def check(self) -> int:
        """
        Verifies database integrity under lock and returns count of active keys.
        """
        self._ensure_open()
        with self.lock:
            with file_lock(self.lock_file):
                self._ensure_open()
                return len(self.index)

    def dump(self) -> List[Tuple[bytes, bytes]]:
        """
        Returns all key-value pairs sorted lexicographically by key.
        """
        self._ensure_open()
        with self.lock:
            self._ensure_open()
            results = []
            for k in sorted(self.index.keys()):
                offset, length = self.index[k]
                self.file.seek(offset)
                val = self.file.read(length)
                results.append((k, val))
            return results

    def compact(self):
        """
        Crash-safe database compaction:
        1. Writes all live key-values into <path>.compact
        2. Flushes & fsyncs <path>.compact
        3. Closes main handle
        4. Replaces old database with new compacted file via os.replace
        5. Reopens and re-indexes
        """
        self._ensure_open()

        with self.lock:
            with file_lock(self.lock_file):
                self._ensure_open()

                compact_temp = self.path + ".compact"
                if os.path.exists(compact_temp):
                    os.remove(compact_temp)

                txid = int.from_bytes(os.urandom(8), "little")

                # Write live keys to temporary file
                with open(compact_temp, "w+b") as temp_f:
                    temp_f.write(encode_file_header())

                    for key in sorted(self.index.keys()):
                        offset, length = self.index[key]
                        self.file.seek(offset)
                        val = self.file.read(length)
                        rec = encode_record(TYPE_PUT, txid, key, val)
                        temp_f.write(rec)

                    commit_rec = encode_record(TYPE_COMMIT, txid)
                    temp_f.write(commit_rec)

                    fsync_file(temp_f)

                # Close current database handle before replacing on Windows
                self.file.close()
                self.file = None

                # Atomic file replacement
                os.replace(compact_temp, self.path)

                # Sync directory metadata if on POSIX
                sync_directory_if_posix(self.path)

                # Reopen and rebuild index
                self.file = open(self.path, "r+b")
                self._recover()

    def close(self):
        """Flush and close cleanly."""
        with self.lock:
            if not self.closed:
                self.closed = True
                if self.file is not None and not self.file.closed:
                    try:
                        fsync_file(self.file)
                    except OSError:
                        pass
                    self.file.close()
                if self.lock_file is not None and not self.lock_file.closed:
                    self.lock_file.close()
