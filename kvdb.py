
import os
import struct
import zlib
import argparse
import threading
from contextlib import contextmanager

# ==============================
# DATABASE CONFIGURATION
# ==============================

MAGIC = b"KV01"
HEADER = struct.Struct(">4sBIIQ")
CRC = struct.Struct(">I")

PUT = 1
DELETE = 2
COMMIT = 3

MAX_KEY = 1024
MAX_VALUE = 1024 * 1024
MAX_BATCH = 10000

GLOBAL_LOCK = threading.RLock()


# ==============================
# CROSS-PROCESS FILE LOCK
# ==============================

@contextmanager
def file_lock(lock_file):

    with GLOBAL_LOCK:
        lock_file.seek(0)

        if os.name == "nt":
            import msvcrt
            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_LOCK,
                1
            )
        else:
            import fcntl
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX
            )

        try:
            yield

        finally:
            lock_file.seek(0)

            if os.name == "nt":
                msvcrt.locking(
                    lock_file.fileno(),
                    msvcrt.LK_UNLCK,
                    1
                )
            else:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_UN
                )


# ==============================
# DATABASE ENGINE
# ==============================

class KVDatabase:

    def __init__(self, filename="database.kv"):

        self.filename = filename
        self.lock = threading.RLock()
        self.index = {}

        self.file = open(filename, "a+b")

        # Separate lock file
        self.lock_file = open(filename + ".lock", "a+b")

        if os.path.getsize(filename + ".lock") == 0:
            self.lock_file.write(b"\0")
            self.lock_file.flush()
            os.fsync(self.lock_file.fileno())

        with self.lock:
            with file_lock(self.lock_file):
                self._recover()

    # ==========================
    # CREATE DATABASE RECORD
    # ==========================

    def _record(self, op, key=b"", value=b"", txid=0):

        header = HEADER.pack(
            MAGIC,
            op,
            len(key),
            len(value),
            txid
        )

        payload = key + value

        checksum = CRC.pack(
            zlib.crc32(header + payload) & 0xffffffff
        )

        return header + payload + checksum

    # ==========================
    # DATABASE RECOVERY
    # ==========================

    def _recover(self):

        self.index.clear()
        self.file.seek(0)

        pending = {}
        valid_end = 0

        while True:

            start = self.file.tell()
            header = self.file.read(HEADER.size)

            if not header:
                valid_end = start
                break

            if len(header) != HEADER.size:
                break

            magic, op, klen, vlen, txid = HEADER.unpack(header)

            if magic != MAGIC:
                raise ValueError("Invalid database magic")

            if op not in (PUT, DELETE, COMMIT):
                raise ValueError("Invalid operation")

            if op in (PUT, DELETE):
                if not 1 <= klen <= MAX_KEY:
                    raise ValueError("Invalid key length")

            if op == PUT and vlen > MAX_VALUE:
                raise ValueError("Value too large")

            if op in (DELETE, COMMIT) and vlen != 0:
                raise ValueError("Invalid record length")

            if op == COMMIT and klen != 0:
                raise ValueError("Invalid commit record")

            payload = self.file.read(klen + vlen)
            checksum = self.file.read(CRC.size)

            if len(payload) != klen + vlen:
                break

            if len(checksum) != CRC.size:
                break

            expected = zlib.crc32(header + payload) & 0xffffffff
            actual = CRC.unpack(checksum)[0]

            if expected != actual:

                # Distinguish a bad final record from
                # corruption followed by more records.
                if self.file.read(1):
                    raise ValueError(
                        "Corruption inside database"
                    )

                break

            if op == COMMIT:

                actions = pending.pop(txid, [])

                for action, key, value, offset in actions:

                    if action == PUT:
                        self.index[key] = (
                            offset,
                            len(value)
                        )

                    else:
                        self.index.pop(key, None)

            elif op == PUT:

                key = payload[:klen]
                value = payload[klen:]

                pending.setdefault(txid, []).append(
                    (
                        PUT,
                        key,
                        value,
                        start + HEADER.size + klen
                    )
                )

            elif op == DELETE:

                key = payload[:klen]

                pending.setdefault(txid, []).append(
                    (DELETE, key, b"", 0)
                )

            valid_end = self.file.tell()

        # Remove incomplete trailing bytes
        self.file.truncate(valid_end)
        self.file.flush()
        os.fsync(self.file.fileno())

        self.file.seek(0, os.SEEK_END)

    # ==========================
    # BATCH TRANSACTION
    # ==========================

    def batch(self, operations):

        if not 1 <= len(operations) <= MAX_BATCH:
            raise ValueError(
                "Batch must contain 1–10000 operations"
            )

        with self.lock:

            with file_lock(self.lock_file):

                # Refresh index
                self._recover()

                txid = int.from_bytes(
                    os.urandom(8),
                    "big"
                )

                records = []

                # Validate and prepare all records
                for action, key, value in operations:

                    key = (
                        key.encode()
                        if isinstance(key, str)
                        else key
                    )

                    value = (
                        value.encode()
                        if isinstance(value, str)
                        else value
                    )

                    if not 1 <= len(key) <= MAX_KEY:
                        raise ValueError("Invalid key length")

                    if action == "put":

                        if len(value) > MAX_VALUE:
                            raise ValueError("Value exceeds 1 MB")

                        records.append(
                            self._record(
                                PUT, key, value, txid
                            )
                        )

                    elif action == "delete":

                        records.append(
                            self._record(
                                DELETE, key, b"", txid
                            )
                        )

                    else:
                        raise ValueError("Unknown operation")

                # Add transaction commit marker
                records.append(
                    self._record(COMMIT, txid=txid)
                )

                # Append and synchronize
                self.file.seek(0, os.SEEK_END)

                self.file.write(b"".join(records))
                self.file.flush()
                os.fsync(self.file.fileno())

                # Rebuild index after commit
                self._recover()

    # ==========================
    # PUT
    # ==========================

    def put(self, key, value):

        self.batch([
            ("put", key, value)
        ])

    # ==========================
    # DELETE
    # ==========================

    def delete(self, key):

        self.batch([
            ("delete", key, "")
        ])

    # ==========================
    # GET
    # ==========================

    def get(self, key):

        key = (
            key.encode()
            if isinstance(key, str)
            else key
        )

        with self.lock:

            with file_lock(self.lock_file):

                self._recover()

                if key not in self.index:
                    return None

                offset, length = self.index[key]

                self.file.seek(offset)

                return self.file.read(length)

    # ==========================
    # DATABASE CHECK
    # ==========================

    def check(self):

        with self.lock:

            with file_lock(self.lock_file):

                self._recover()

                return len(self.index)

    # ==========================
    # DATABASE COMPACTION
    # ==========================

    def compact(self):

        with self.lock:

            with file_lock(self.lock_file):

                self._recover()

                temp_file = self.filename + ".compact.tmp"

                txid = int.from_bytes(
                    os.urandom(8),
                    "big"
                )

                try:

                    # Write live records to temporary file
                    with open(temp_file, "wb") as temp:

                        for key, (offset, length) in self.index.items():

                            self.file.seek(offset)
                            value = self.file.read(length)

                            if len(value) != length:
                                raise IOError(
                                    "Could not read database value"
                                )

                            record = self._record(
                                PUT,
                                key,
                                value,
                                txid
                            )

                            temp.write(record)

                        # Commit compacted records
                        temp.write(
                            self._record(COMMIT, txid=txid)
                        )

                        temp.flush()
                        os.fsync(temp.fileno())

                    # Close old database before replacement
                    self.file.close()
                    self.file = None

                    # Replace old database
                    os.replace(
                        temp_file,
                        self.filename
                    )

                    # Reopen new database
                    self.file = open(
                        self.filename,
                        "a+b"
                    )

                    # Synchronize directory metadata on POSIX
                    if os.name != "nt":

                        directory = os.path.dirname(
                            os.path.abspath(self.filename)
                        )

                        fd = os.open(directory, os.O_RDONLY)

                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)

                    self._recover()

                    print("Compaction completed successfully")

                finally:

                    # Ensure database handle is open
                    if self.file is None or self.file.closed:
                        self.file = open(
                            self.filename,
                            "a+b"
                        )

                    if os.path.exists(temp_file):
                        os.remove(temp_file)

    # ==========================
    # CLOSE DATABASE
    # ==========================

    def close(self):

        with self.lock:

            if self.file is not None and not self.file.closed:
                self.file.close()

            if not self.lock_file.closed:
                self.lock_file.close()


# ==============================
# COMMAND LINE INTERFACE
# ==============================

def main():

    parser = argparse.ArgumentParser(
        description="Synterra Key Value Database"
    )

    parser.add_argument(
        "--db",
        default="database.kv",
        help="Database file path"
    )

    parser.add_argument(
        "command",
        choices=[
            "put",
            "get",
            "delete",
            "check",
            "compact"
        ]
    )

    parser.add_argument("key", nargs="?")
    parser.add_argument("value", nargs="?")

    args = parser.parse_args()

    # Validate arguments before opening database
    if args.command == "put":
        if args.key is None or args.value is None:
            parser.error("put requires KEY VALUE")

    elif args.command in ("get", "delete"):
        if args.key is None:
            parser.error(
                f"{args.command} requires KEY"
            )

    db = KVDatabase(args.db)

    try:

        if args.command == "put":

            db.put(args.key, args.value)
            print("ACK: Data saved")

        elif args.command == "get":

            value = db.get(args.key)

            if value is None:
                print("NOT FOUND")
            else:
                print(value.decode())

        elif args.command == "delete":

            db.delete(args.key)
            print("ACK: Data deleted")

        elif args.command == "check":

            print("Database OK")
            print("Total keys:", db.check())

        elif args.command == "compact":

            db.compact()

    finally:
        db.close()


if __name__ == "__main__":
    main()
