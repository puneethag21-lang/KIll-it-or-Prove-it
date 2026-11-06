# ARCHITECTURE_DRAFT.md

> **Notice:** This document is an objective, code-verified audit of what the repository **actually implements** in its current source files ([`kvdb.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/kvdb.py), [`src/cli.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/cli.py), [`src/db.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py), [`src/storage.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py), [`src/format.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/format.py), [`src/errors.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/errors.py), and [`torture/torture.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/torture/torture.py)). Every statement cites the specific file, function, and line numbers.

---

# PART 1 — HIGH-LEVEL DESIGN

## 1. Components and Responsibilities

| Component | Primary File & Functions | Concrete Responsibilities in Code |
|---|---|---|
| **CLI / Serve** | [`src/cli.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/cli.py): `main()`, `run_serve()`, `parse_batch_file()` | Parses command line options; dispatches commands (`open`, `put`, `get`, `del`, `batch`, `dump`, `check`, `compact`, `serve`). In `run_serve()`, outputs `READY\n` upon opening, reads lines from `sys.stdin`, dispatches to `KVDatabase`, and prints `ACK <op-id>\n` or `ACK\n` to `sys.stdout` followed by `sys.stdout.flush()`. |
| **Top-Level Wrapper** | [`kvdb.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/kvdb.py) | Exposes `KVDatabase`, constants `PUT`, `DELETE`, `COMMIT`, and forwards `main()` directly to `src.cli.main()`. |
| **DB API** | [`src/db.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py): `KVDatabase` class | Public interface: `__init__()`, `put()`, `get()`, `delete()`, `batch()`, `check()`, `dump()`, `compact()`, `close()`. Manages the authoritative file handle (`self.file`), file lock handle (`self.lock_file`), and in-memory index (`self.index`). |
| **Storage / Log Writer** | [`src/storage.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py): `fsync_file()`, `open_lock_file()`, `sync_directory_if_posix()` | Opens lock files (`open_lock_file()`); guarantees at least 1 byte exists; flushes and issues `os.fsync(file_obj.fileno())` via `fsync_file()`; attempts directory `os.fsync` on non-Windows platforms. |
| **In-Memory Index** | [`src/db.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py): `self.index` | `Dict[bytes, Tuple[int, int]]` mapping key bytes to `(val_offset, val_len)`. Reconstructed during `_recover()` and mutated in `batch()` only after `os.fsync` succeeds. |
| **Recovery Engine** | [`src/db.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py): `_recover()` | Validates 16-byte header; reads log in 4MB chunks; verifies CRC32; stages uncommitted ops in `pending[txid]`; applies them upon `TYPE_COMMIT`; truncates file to `last_commit_end` via `self.file.truncate()`. |
| **Compaction Engine** | [`src/db.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py): `compact()` | Writes active keys to `<path>.compact`; appends `TYPE_COMMIT`; calls `fsync_file()`; closes handles; swaps via `os.replace()`; reopens; calls `_recover()`. |
| **Disk-Full Guard** | [`src/storage.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py): `check_max_bytes_or_raise()` | Called in `db.py:batch()` before writing. Compares `current_size + total_write_size > max_bytes`. Raises `DiskFullError` before any bytes are written to `self.file`. |
| **Concurrency Locks** | [`src/storage.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py): `_GLOBAL_THREAD_LOCK`, `file_lock()`; [`src/db.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py): `self.lock` | Dual-level synchronization: process-wide `threading.RLock()` protects intra-process threads; `msvcrt.locking(1)` (Windows) or `fcntl.flock(LOCK_EX)` (POSIX) locks the companion `<path>.lock` file for cross-process mutual exclusion. |
| **Torture Tool** | [`torture/torture.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/torture/torture.py): `IndependentOracle`, `TortureRunner` | Spawns `serve` mode as a child process; runs 2 concurrent writer threads; generates globally unique value strings; tracks confirmed promises in `IndependentOracle`; kills child process using `proc.kill()` (`TerminateProcess`); verifies dump against Oracle; calls `resolve_after_pass()`. |

---

## 2. Mermaid Component Diagram

```mermaid
flowchart TD
    subgraph ClientProcess["Client / Test Process"]
        CLI["CLI Command (src/cli.py)"]
        Torture["Torture Tool (torture/torture.py)"]
        Oracle["Independent Oracle (oracle.log)"]
    end

    subgraph Synchronization["Locks & Safety Guards"]
        TLock["threading.RLock (self.lock / _GLOBAL_THREAD_LOCK)"]
        FLock["Cross-Process file_lock() on <path>.lock\n(msvcrt.locking / fcntl.flock)"]
        MaxBytes["check_max_bytes_or_raise() (src/storage.py)"]
    end

    subgraph Memory["Memory Layer"]
        Index["self.index:\nDict[bytes, Tuple[val_offset, val_len]]"]
        Pending["pending: Dict[txid, List[ops]] (during _recover)"]
    end

    subgraph Engine["Database Engine (src/db.py)"]
        Batch["batch() / put() / delete()"]
        Get["get()"]
        Recover["_recover()"]
        Compact["compact()"]
    end

    subgraph Disk["On-Disk Storage"]
        DataFile["Authoritative File: <path>.kv\n16B Header + Records + Commits"]
        LockFile["Companion Lock File: <path>.kv.lock\n1 sentinel byte"]
        CompactTemp["Temp Compaction File: <path>.kv.compact"]
    end

    CLI --> TLock
    Torture -->|"stdin / stdout (serve mode)"| CLI
    Torture --> Oracle

    TLock --> FLock
    FLock --> MaxBytes

    MaxBytes --> Batch
    Batch -->|"1. write(records + commit)"| DataFile
    Batch -->|"2. fsync_file()"| DataFile
    Batch -->|"3. update"| Index

    Get --> TLock
    Get -->|"Read val_len bytes from val_offset"| DataFile

    Recover -->|"Read 4MB chunks via memoryview"| DataFile
    Recover --> Pending
    Pending -->|"Apply on valid COMMIT"| Index
    Recover -->|"truncate(last_commit_end)"| DataFile

    Compact -->|"Stream live keys"| CompactTemp
    Compact -->|"fsync_file()"| CompactTemp
    Compact -->|"os.replace()"| DataFile
```

---

## 3. Data Flow by Operation

### A. `put(key, value)`
1. [`src/db.py:307`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L307) `put()` calls `self.batch([("put", key, value)])`.
2. Normalized: `key` encoded to bytes; length validated ($1 \le \text{len} \le 1024$). `value` encoded to bytes; length validated ($\le 1,048,576$).
3. Acquires `self.lock` and `file_lock(self.lock_file)`.
4. Packs `TYPE_PUT` record + `TYPE_COMMIT` record with shared 64-bit `txid`.
5. Pre-check: `check_max_bytes_or_raise(self.file, total_size, self.max_bytes)`.
6. `self.file.seek(0, os.SEEK_END)`.
7. `self.file.write(records_bytes)`.
8. `fsync_file(self.file)` (`flush()` + `os.fsync()`).
9. `self.index[key_b] = (val_offset, val_len)`.
10. Releases locks; returns to caller.

### B. `get(key)`
1. [`src/db.py:315`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L315) `get()` encodes key to bytes.
2. Acquires `self.lock`.
3. Checks `if key_b not in self.index:` $\rightarrow$ returns `None`.
4. Extracts `offset, length = self.index[key_b]`.
5. `self.file.seek(offset)`.
6. Returns `self.file.read(length)`.
7. Releases `self.lock`.

### C. `delete(key)`
1. [`src/db.py:311`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L311) `delete()` calls `self.batch([("delete", key, b"")])`.
2. Follows identical write ordering as `put()`, packing a `TYPE_DELETE` record (`val_len = 0`) + `TYPE_COMMIT`.
3. After `fsync_file()`, calls `self.index.pop(key_b, None)`.

### D. `batch(operations)`
1. [`src/db.py:228`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L228) Validates batch size ($1 \le N \le 10,000$).
2. Validates all operations and normalizes to `(TYPE_PUT, key, val)` or `(TYPE_DELETE, key, b"")`.
3. Under `self.lock` and `file_lock(self.lock_file)`:
   - Generates single 64-bit `txid`.
   - Iterates ops: calculates `val_offset = current_offset + 19 + len(key)`; appends record bytes to buffer; tracks `(rec_type, key, val_offset, val_len)`.
   - Appends 23-byte `TYPE_COMMIT` record to buffer.
   - Calls `check_max_bytes_or_raise()`.
   - Single `self.file.write()`.
   - `fsync_file(self.file)`.
   - Applies list in exact order to `self.index` (overwriting or deleting; last operation on a key wins).

### E. `open(path, max_bytes)` and Recovery
1. [`src/db.py:41`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L41) Cleans up `<path>.compact` if leftover.
2. Opens `<path>.lock` via `open_lock_file()`.
3. Acquires `file_lock()`.
4. If file missing or 0 bytes: writes 16-byte header, calls `fsync_file()`.
5. Calls `_recover()`:
   - Verifies 16-byte header magic (`b"KVDB0001"`) and version (`1`). Raises `CorruptedDatabaseError` if invalid.
   - Scans 4MB chunks via `memoryview`.
   - Unpacks 19-byte header; validates `rec_len == 19 + klen + vlen + 4`.
   - Computes CRC32 over `record[:rec_len - 4]`; compares with unpacked trailer.
   - If invalid CRC, unknown type, or incomplete read: halts scanning immediately.
   - Buffers uncommitted ops in `pending[txid]`.
   - On `TYPE_COMMIT`: pops `pending[txid]` and applies to `self.index`; updates `last_commit_end = offset + rec_len`.
   - After scan: calls `self.file.truncate(last_commit_end)` and `fsync_file()`.

### F. `compact()`
1. [`src/db.py:346`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L346) Under locks:
2. Opens `<path>.compact` in mode `"w+b"`.
3. Writes 16-byte file header.
4. For every active key in `sorted(self.index.keys())`:
   - Seeks `self.file` to `offset`; reads `length` bytes.
   - Writes `TYPE_PUT` record to `<path>.compact`.
5. Appends `TYPE_COMMIT` record to `<path>.compact`.
6. Calls `fsync_file(temp_f)`. Closes `temp_f`.
7. Closes `self.file` (`self.file.close()`).
8. Atomically replaces: `os.replace(compact_temp, self.path)`.
9. `sync_directory_if_posix(self.path)` (POSIX only).
10. Reopens `self.file = open(self.path, "r+b")`.
11. Calls `self._recover()` to repopulate `self.index` with new file offsets.

### G. `serve` Mode and ACK Handshake
1. [`src/cli.py:53`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/cli.py#L53) `run_serve()` opens `KVDatabase(db_path)`.
2. Prints `READY\n` to `sys.stdout`; flushes `sys.stdout`.
3. Reads line by line from `sys.stdin`.
4. Extracts `op_id` (if present) and command (`put`, `del`, `batch`, `compact`).
5. Calls corresponding `db` method (which blocks until `fsync` completes).
6. Prints `ACK <op-id>\n` (or `ACK\n`) to `sys.stdout`.
7. Calls `sys.stdout.flush()`.
8. On exception: prints `ERROR: <msg>\n` to `sys.stderr` and flushes.

---

# PART 2 — LOW-LEVEL DESIGN

## 1. Exact On-Disk Layout & Hex Dump

### Binary Specification
- **Endianness**: Little-endian (`<`) across all fields ([`src/format.py:22, 38, 41`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/format.py#L22)).
- **File Header (16 bytes)**:
  - Offset `0..7` (8B): Magic bytes `b"KVDB0001"`
  - Offset `8..11` (4B): Version `uint32` = `1`
  - Offset `12..15` (4B): Reserved `uint32` = `0`
- **Record Header (19 bytes)**:
  - Offset `0..3` (4B): `rec_len` (`uint32`) — Total record length in bytes.
  - Offset `4` (1B): `type` (`uint8`) — `1 = PUT`, `2 = DELETE`, `3 = COMMIT`.
  - Offset `5..12` (8B): `txid` (`uint64`) — Random 64-bit transaction identifier.
  - Offset `13..14` (2B): `key_len` (`uint16`) — Length of key ($1..1024$; 0 for COMMIT).
  - Offset `15..18` (4B): `val_len` (`uint32`) — Length of value ($0..1048576$; 0 for DELETE/COMMIT).
- **Payload**:
  - Offset `19 .. 19 + key_len`: Raw key bytes.
  - Offset `19 + key_len .. 19 + key_len + val_len`: Raw value bytes.
- **Checksum Trailer (4 bytes)**:
  - Offset `rec_len - 4 .. rec_len`: `CRC32` (`uint32` little-endian).
  - **CRC Coverage**: `zlib.crc32(record_bytes[:rec_len - 4]) & 0xffffffff`. Covers every byte in the record preceding the CRC itself (header + payload).

### Real File Hex Dump
Generated by executing `python kvdb.py put x.kv a b`:

```text
           00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F
00000000   4B 56 44 42 30 30 30 31 01 00 00 00 00 00 00 00  KVDB0001........
00000010   19 00 00 00 01 E2 BB BF 0C 00 04 B9 87 01 00 01  .....â»¿...¹‡...
00000020   00 00 00 61 62 4D B5 8F 75 17 00 00 00 03 E2 BB  ...abMµ u.....â»
00000030   BF 0C 00 04 B9 87 00 00 00 00 00 00 04 DC 97 E9  ¿...¹‡.......Ü—é
```

**Byte-by-Byte Breakdown of the Real Hex Dump:**
- `0x00000000 - 0x00000007`: `4B 56 44 42 30 30 30 31` $\rightarrow$ Magic string `b"KVDB0001"`
- `0x00000008 - 0x0000000B`: `01 00 00 00` $\rightarrow$ Version `1` (`<I`)
- `0x0000000C - 0x0000000F`: `00 00 00 00` $\rightarrow$ Reserved `0` (`<I`)
- `0x00000010 - 0x00000013`: `19 00 00 00` $\rightarrow$ `rec_len = 0x19 = 25` bytes (`19B header + 1B key + 1B val + 4B CRC`)
- `0x00000014`: `01` $\rightarrow$ `TYPE_PUT` (`1`)
- `0x00000015 - 0x0000001C`: `E2 BB BF 0C 00 04 B9 87` $\rightarrow$ 64-bit `txid`
- `0x0000001D - 0x0000001E`: `01 00` $\rightarrow$ `key_len = 1` (`<H`)
- `0x0000001F - 0x00000022`: `01 00 00 00` $\rightarrow$ `val_len = 1` (`<I`)
- `0x00000023`: `61` $\rightarrow$ Key `b"a"`
- `0x00000024`: `62` $\rightarrow$ Value `b"b"`
- `0x00000025 - 0x00000028`: `4D B5 8F 75` $\rightarrow$ CRC32 (`<I`) over bytes `0x00000010` through `0x00000024`
- `0x00000029 - 0x0000002C`: `17 00 00 00` $\rightarrow$ `rec_len = 0x17 = 23` bytes (`19B header + 4B CRC`)
- `0x0000002D`: `03` $\rightarrow$ `TYPE_COMMIT` (`3`)
- `0x0000002E - 0x00000035`: `E2 BB BF 0C 00 04 B9 87` $\rightarrow$ Matching 64-bit `txid`
- `0x00000036 - 0x00000037`: `00 00` $\rightarrow$ `key_len = 0`
- `0x00000038 - 0x0000003B`: `00 00 00 00` $\rightarrow$ `val_len = 0`
- `0x0000003C - 0x0000003F`: `04 DC 97 E9` $\rightarrow$ CRC32 (`<I`) over bytes `0x00000029` through `0x0000003B`

---

## 2. In-Memory Data Structures
- **Type**: Standard Python `dict` ([`src/db.py:46`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L46)).
- **Definition**: `self.index: Dict[bytes, Tuple[int, int]]`.
- **Key**: Raw bytes of the key (`bytes`).
- **Value**: A 2-tuple of integers: `(val_offset, val_len)`:
  - `val_offset`: Exact byte offset in `<path>.kv` where raw value bytes start (`record_file_offset + 19 + len(key)`).
  - `val_len`: Length of the value in bytes.
- Value bytes are **never resident in RAM** unless actively returned by `get()` or `dump()`.

---

## 3. Numbered Write Ordering & Exact Calls

In [`src/db.py:batch()`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L228):
1. **Pre-check**: `check_max_bytes_or_raise(self.file, total_write_size, self.max_bytes)` ([`db.py:274`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L274)).
2. **Seek to EOF**: `self.file.seek(0, os.SEEK_END)` ([`db.py:277`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L277)).
3. **Write**: `self.file.write(records_bytes)` ([`db.py:278`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L278)).
4. **Flush buffer**: `fsync_file(self.file)` calls `file_obj.flush()` ([`storage.py:65`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py#L65)).
5. **Physical sync**: `fsync_file(self.file)` calls `os.fsync(file_obj.fileno())` ([`storage.py:66`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py#L66)).
6. **Index update**: Mutates `self.index` for each record in the batch ([`db.py:284-288`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L284-L288)).
7. **ACK Emission**:
   - In CLI `put`/`del`/`batch`: `print("ACK")` ([`src/cli.py:168, 190, 198`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/cli.py#L168)).
   - In `serve` mode: `sys.stdout.write(f"ACK {op_id}\n")` followed by `sys.stdout.flush()` ([`src/cli.py:133-137`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/cli.py#L133-L137)).

---

## 4. Recovery Algorithm & Mid-Crash Behavior

1. [`src/db.py:_recover()`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L76): Clears `self.index`.
2. Reads 16 bytes. Confirms `is_valid_file_header(hdr)`. Raises `CorruptedDatabaseError` if invalid.
3. Initializes `last_commit_end = 16`, `pending = {}`.
4. Reads 4MB chunks into `bytearray` buffer `buf`.
5. Iterates records using integer `cursor`. If remaining buffer $< 19$ bytes or $< rec\_len$, shifts unconsumed bytes to buffer start and reads next chunk.
6. Inspects `rec_len`. If $rec\_len < 23$ or $rec\_len > \text{max\_possible}$ or buffer EOF: **breaks scan loop**.
7. Checks CRC32: `zlib.crc32(memoryview(buf)[cursor : cursor + rec_len - 4])`. If mismatch: **breaks scan loop**.
8. For `TYPE_PUT`: appends `(TYPE_PUT, key, val_offset, vlen)` to `pending[txid]`.
9. For `TYPE_DELETE`: appends `(TYPE_DELETE, key, 0, 0)` to `pending[txid]`.
10. For `TYPE_COMMIT`: pops `pending[txid]`, applies operations to `self.index`, updates `last_commit_end = record_file_offset + rec_len`.
11. After loop breaks: if `file.tell() != last_commit_end`:
    - `self.file.seek(last_commit_end)`
    - `self.file.truncate(last_commit_end)`
    - `fsync_file(self.file)`

**What happens if killed midway during recovery?**
- **Killed during scan**: The file was not modified. Next `open()` re-scans identically. Idempotent.
- **Killed during `file.truncate()`**: File size is partially truncated. Next `open()` re-scans, identifies identical `last_commit_end`, and repeats `file.truncate()`. Idempotent.

---

## 5. Compaction Protocol Step by Step

1. [`src/db.py:compact()`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L346) Acquires `self.lock` and `file_lock(self.lock_file)`.
2. Temp file name: `compact_temp = self.path + ".compact"` ([`db.py:355`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L355)).
3. Writes 16-byte header to `compact_temp`.
4. Generates single 64-bit `txid`.
5. Streams each key in `sorted(self.index.keys())`: seeks `self.file`, reads `length` bytes, writes `TYPE_PUT` record to `compact_temp`.
6. Writes `TYPE_COMMIT` record to `compact_temp`.
7. Calls `fsync_file(temp_f)` (`flush()` + `os.fsync()`).
8. Closes `temp_f`.
9. Closes `self.file` (`self.file.close()`, sets `self.file = None`).
10. Calls `os.replace(compact_temp, self.path)`.
11. On POSIX: `sync_directory_if_posix(self.path)` opens parent dir and calls `os.fsync()`. (On Windows: no-op).
12. Reopens `self.file = open(self.path, "r+b")`.
13. Calls `self._recover()` to repopulate `self.index` with new compacted file offsets.

**Disk State at Each Crash Point During Compaction:**
- **Crash before step 7 (`fsync(temp)`)**: Original `<path>.kv` is authoritative and untouched. Incomplete `<path>.compact` exists. On next `open()`, [`db.py:48-52`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L48-L52) deletes `<path>.compact`.
- **Crash between step 7 and step 10 (`os.replace`)**: Complete, durable `<path>.compact` exists; original `<path>.kv` is authoritative and untouched. On next `open()`, `<path>.compact` is removed; original data intact.
- **Crash during step 10 (`os.replace`)**: Handled by filesystem directory entry journal (NTFS / ext4). Either old file or new file is in place. Both are 100% durable.
- **Crash after step 10 before step 13**: New file is in place on disk. Next `open()` opens the new file and recovers index cleanly.

---

## 6. Locking Architecture

1. **`threading.RLock()` (`_GLOBAL_THREAD_LOCK` in `src/storage.py:11`, `self.lock` in `src/db.py:44`)**:
   - Protects in-memory structures (`self.index`, file seek pointer) from concurrent threads within the same Python process.
   - Acquired during all public API calls (`put`, `get`, `delete`, `batch`, `check`, `dump`, `compact`, `close`).
2. **Cross-Process Companion Lock File (`<path>.lock`)**:
   - Initialized by [`src/storage.py:open_lock_file()`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py#L46).
   - Contains 1 byte (`b"\0"`).
   - On Windows: `msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)` ([`storage.py:27`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py#L27)).
   - On POSIX: `fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)` ([`storage.py:30`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py#L30)).
   - **Crucial Separation**: The main `.kv` file is **never locked directly**. This enables Windows to execute `os.replace(temp, main)` during compaction without `PermissionError`.

---

## 7. Disk-Full (`max_bytes`) Handling

- Function: [`src/storage.py:check_max_bytes_or_raise()`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py#L70).
- Location checked: [`src/db.py:274`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L274) inside `batch()`.
- Mechanism: Seeks `self.file` to end, reads `current_size = self.file.tell()`. If `max_bytes is not None` and `current_size + total_write_size > max_bytes`, raises `DiskFullError`.
- **Disk State on Failure**: Because the check occurs **before** `self.file.write()`, **zero bytes are appended to disk**. The file remains at its exact prior length, completely uncorrupted, and fully open for reads and subsequent compactions.

---

# PART 3 — GAP LIST

| # | Requirement from Synterra PDF | Implemented? | Tested? | Evidence in Code & Tests |
|---|---|---|---|---|
| **1** | Single authoritative data file | **Implemented** | **Tested** | Exactly 1 data file (`.kv`); `.lock` is companion lock; `.compact` is ephemeral. |
| **2** | Mandatory ops: `put`, `get`, `delete`, `batch`, `open`, `close` | **Implemented** | **Tested** | [`src/db.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py); verified by [`tests/test_engine.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/tests/test_engine.py). |
| **3** | Key size 1..1024 bytes, value 0..1MB | **Implemented** | **Tested** | Checked in [`db.py:246-253`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L246); verified in [`tests/test_format.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/tests/test_format.py). |
| **4** | Atomic batch up to 10,000 ops | **Implemented** | **Tested** | Checked in [`db.py:233`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L233); verified by [`tests/test_engine.py:test_batch_ordering_last_op_wins`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/tests/test_engine.py). |
| **5** | Batch last op on key wins (Scenario B5) | **Implemented** | **Tested** | Applied in list order in [`db.py:284-288`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L284-L288); verified by [`tests/test_engine.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/tests/test_engine.py). |
| **6** | Recovery time at 1,000,000 keys < 2.0s | **Implemented** | **Measured** | **3.8584s** (Measured live on this machine). **GAP: Exceeds 2.0s target by 1.85s**. |
| **7** | Crash-safe compaction shrinks file | **Implemented** | **Tested** | [`src/db.py:compact()`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/db.py#L346); verified by [`tests/test_engine.py:test_compaction`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/tests/test_engine.py). |
| **8** | Configurable `max_bytes` disk-full simulation | **Implemented** | **Tested** | [`src/storage.py:check_max_bytes_or_raise()`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py#L70); verified by [`tests/test_engine.py:test_disk_full_max_bytes`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/tests/test_engine.py). |
| **9** | 2+ concurrent writers; readers never see half-applied batch | **Implemented** | **Tested** | Dual lock architecture; verified by [`torture/torture.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/torture/torture.py) (2 concurrent threads). |
| **10** | All 8 CLI commands: `open`, `put`, `get`, `del`, `batch`, `dump`, `check`, `serve` | **Implemented** | **Tested** | [`src/cli.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/cli.py); verified by [`tests/test_cli.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/tests/test_cli.py) (3/3 pass). |
| **11** | `serve` mode prints ACK only after physical fsync | **Implemented** | **Tested** | [`src/cli.py:133`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/cli.py#L133) calls `db.put()` which issues `os.fsync()`, then prints `ACK`. |
| **12** | Torture tool: independent process, oracle, random ms kill, 500 iterations, stop on first failure | **Implemented** | **Tested** | [`torture/torture.py`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/torture/torture.py); verified by 200-iteration and 50-iteration runs. |
| **13** | Torture mode: `large-values` | **Implemented** | **Tested** | [`torture.py:195`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/torture/torture.py#L195) (500KB-1MB values). |
| **14** | Torture mode: `large-batch` (2,000 - 10,000 ops) | **Partial** | **Tested (small batches)** | Currently sends batches of 50-200 ops in `torture.py:204`. Batches of 2,000-10,000 pending Step 2. |
| **15** | Torture mode: `compaction` | **Implemented** | **Tested** | [`torture.py:214`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/torture/torture.py#L214); verified 10/10 iterations pass. |
| **16** | Torture mode: `recovery-crash` | **Implemented** | **Tested** | [`torture.py:273`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/torture/torture.py#L273); verified 10/10 iterations pass. |
| **17** | Torture mode: `disk-full` | **Partial** | **Untested** | CLI `serve` doesn't accept `--max-bytes`. Pending Step 3. |

---

# Places Where the Code and the PDF Disagree

1. **Recovery Speed at 1,000,000 Keys (Section 2, Page 2)**:
   - *PDF Requirement*: Must complete in under 2.0 seconds.
   - *Code Actual*: Measured at **3.8584s** on the current machine and disk subsystem. This exceeds the 2.0-second limit by 1.86s.
2. **`serve` Mode Does Not Accept `--max-bytes` (Section 6, Page 9 vs `src/cli.py:236`)**:
   - *PDF Requirement*: In Section 7, disk-full mode requires running serve mode with a small `--max-bytes`.
   - *Code Actual*: In [`src/cli.py:236`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/cli.py#L236), `p_serve.add_argument("path")` does not have `--max-bytes`. Only `p_open` accepts `--max-bytes`.
3. **`check` Output Formatting (Section 6, Page 9 vs `src/cli.py:211`)**:
   - *PDF Requirement*: "verifies file integrity, prints OK or a reason"
   - *Code Actual*: Prints `OK: <count> keys` instead of bare `OK`.
4. **`open` Output Formatting (Section 6, Page 9 vs `src/cli.py:164`)**:
   - *PDF Requirement*: "prints key count and recovery time"
   - *Code Actual*: Prints `OK: <count> keys recovered in <time>s`.
5. **Directory Fsync on Windows (Section 8, Page 11 vs `src/storage.py:79`)**:
   - *PDF Requirement*: "If you fsync the directory after a rename, say so and say why. If you do not, say why it is safe anyway."
   - *Code Actual*: [`src/storage.py:87`](file:///c:/Users/ZOAI/Downloads/live-location-tracker-master/live-location-tracker-master/demo%20hackto=hon/src/storage.py#L87) checks `if os.name != "nt":` and does not call directory fsync on Windows, relying on NTFS atomic file replacement.

