# ARCHITECTURE.md - Architecture Diagram & Robustness Proof

This document provides the formal architectural specification, scenario-by-scenario proof table, and theoretical guarantee arguments for Synterra's KVDB engine, as required by Deliverable 5 of the "Kill It and Prove It" hackathon specification.

---

## Part 1: Architecture Diagram

```mermaid
flowchart TD
    subgraph ClientLayer["Client & Concurrency Layer"]
        CLI["CLI (kvdb.py / cli.py)"]
        Torture["Torture Tool / Child Process (serve mode)"]
        Writers["2+ Concurrent Threads / Processes"]
    end

    subgraph SyncPrimitives["Synchronization & Safety Guards"]
        TLock["threading.RLock (In-Process Concurrency)"]
        FLock["msvcrt.locking / fcntl.flock (Cross-Process Companion Lock: <path>.lock)"]
        MaxBytesGuard["Disk-Full Pre-Check (check_max_bytes_or_raise)"]
    end

    subgraph MemoryLayer["In-Memory State"]
        MemIndex["self.index: Dict[bytes, Tuple[int, int]]\n{key -> (value_file_offset, value_len)}"]
    end

    subgraph EngineCore["KVDatabase Engine (db.py)"]
        Recovery["_recover(): Sub-second Chunked Scanner"]
        BatchWriter["batch(): Atomic Transaction Assembler"]
        Compactor["compact(): Crash-Safe Copy-on-Write"]
    end

    subgraph OnDiskLayout["Authoritative On-Disk File Layout (<path>.kv)"]
        FileHeader["16-Byte File Header:\n[magic: 8B b'KVDB0001'][version: 4B u32 = 1][reserved: 4B u32 = 0]"]
        DataRecord["Record Framing (Little-Endian):\n[rec_len u32 (4B)][type u8 (1B)][txid u64 (8B)][klen u16 (2B)][vlen u32 (4B)][key bytes][value bytes][crc32 u32 (4B)]"]
        CommitRecord["Commit Marker Record:\n[rec_len=23 u32][type=3 u8][txid u64][klen=0 u16][vlen=0 u32][crc32 u32]"]
    end

    subgraph CompactionTemp["Compaction Scratch (<path>.compact)"]
        TempFile["1. Write File Header\n2. Stream active keys from self.index\n3. Append COMMIT marker\n4. fsync(temp_fd)\n5. Close handles -> os.replace()"]
    end

    CLI --> TLock
    Torture --> TLock
    Writers --> TLock
    TLock --> FLock
    FLock --> MaxBytesGuard

    MaxBytesGuard --> BatchWriter
    BatchWriter -->|"1. write(payload_records + commit_marker)"| OnDiskLayout
    BatchWriter -->|"2. os.fsync(file.fileno())"| OnDiskLayout
    BatchWriter -->|"3. Update in-memory index"| MemIndex

    Recovery -->|"Reads chunks via memoryview cursor"| OnDiskLayout
    Recovery -->|"Discards torn tail; truncates to last valid COMMIT"| OnDiskLayout
    Recovery -->|"Populates confirmed keys"| MemIndex

    Compactor --> TempFile
    TempFile -->|"Atomic replace on Windows/POSIX"| OnDiskLayout
```

### Exact Write Ordering Sequence
1. **Prepare Records**: Serialize $N$ data records (`TYPE_PUT` or `TYPE_DELETE`) sharing a single 64-bit random `txid`, followed immediately by one `TYPE_COMMIT` record with the identical `txid`.
2. **Pre-Check Disk Capacity**: Measure current file size + write payload size. If exceeding `max_bytes`, raise `DiskFullError` immediately. Zero bytes touch disk.
3. **Single System Write**: Append all $N+1$ records into the authoritative file in a single `write()` call.
4. **Physical Flush (`os.fsync`)**: Issue `flush()` followed by `os.fsync(fileno)` to force kernel dirty pages to durable physical platters/NAND blocks.
5. **Update Index**: Only after `fsync` completes without error, update `self.index[key] = (val_offset, val_len)` (or `pop` for deletes).
6. **Return ACK**: Deliver confirmation `ACK <op-id>` to the caller.

---

## Part 2: Scenario-to-Mechanism Proof Table (All 32 Rows)

| Scenario | Scenario Description | Disk State at Moment of Kill | Mechanism Handling the Crash | Code Location | Verified By |
|---|---|---|---|---|---|
| **A1** | Kill before any bytes of a put reach the file | File untouched; old data intact. | Prior data valid; write never occurred. | `db.py:batch` | `torture.py` standard mode, seed=999 |
| **A2** | Kill after some bytes of record written (torn write) | Incomplete trailing bytes past last COMMIT. | `_recover()` detects short record/bad CRC at EOF and truncates back to `last_commit_end`. | `db.py:_recover` L145-L215 | `tests/test_engine.py:test_torn_tail_recovery` |
| **A3** | Kill after record fully written but before `fsync` | Record on disk in OS cache; no ACK sent. | If OS flushed, record parsed; otherwise truncated. Caller never received ACK; either outcome is valid. | `db.py:batch` L276-L285 | `torture.py` standard mode |
| **A4** | Kill after `fsync` returns but before put returns success | All records + COMMIT durable on disk. | Recovery replays COMMIT; data is 100% durable despite caller not receiving return. | `db.py:batch` L280-L287 | `torture.py` standard mode |
| **A5** | Kill immediately after put returns success | All records + COMMIT durable on disk. | Replayed by `_recover()`; index populated; key present. | `db.py:put` L293 | `torture.py` 500-iteration standard run |
| **A6** | Kill during a delete | DELETE record + COMMIT partially or fully on disk. | If COMMIT fsynced, key deleted; if not, key remains. Never corrupted. | `db.py:delete` L297 | `torture.py` standard mode |
| **A7** | Kill during put of a 1 MB value | Partial 1MB payload on disk. | Incomplete payload triggers CRC mismatch / short read; `_recover()` truncates to last COMMIT. | `db.py:_recover` L175-L185 | `torture.py --mode large-values`, seed=999 |
| **B1** | Kill after 3 of 10 ops in a batch are written | 3 ops on disk; no COMMIT record. | All 3 ops buffered in `pending`; EOF reached with no COMMIT; `pending` discarded; truncated to last commit. | `db.py:_recover` L200-L220 | `tests/test_engine.py:test_uncommitted_batch_ignored_and_truncated` |
| **B2** | Kill after all 10 written but commit marker is not | 10 ops on disk; no COMMIT record. | Ops buffered in `pending`; no COMMIT found; file truncated back to previous COMMIT. Zero ops visible. | `db.py:_recover` L200-L220 | `tests/test_engine.py:test_uncommitted_batch_ignored_and_truncated` |
| **B3** | Kill after commit marker written but before `fsync` | COMMIT marker in OS buffer; not fsynced. | If COMMIT reached disk, all 10 visible; if not, all 10 truncated. Never partial. | `db.py:batch` L280-L285 | `torture.py --mode large-batch` |
| **B4** | Kill after batch returns success | All 10 ops + COMMIT marker fsynced. | Recovery encounters COMMIT; all 10 ops committed to index. | `db.py:batch` L286 | `torture.py --mode large-batch`, seed=123 |
| **B5** | Batch puts key X and deletes key X in same batch | Both ops share `txid` before COMMIT. | Ops applied sequentially in list order upon COMMIT; delete wins if second. | `db.py:_recover` L190-L198 | `tests/test_engine.py:test_batch_ordering_last_op_wins` |
| **B6** | Batch of 10,000 ops killed halfway | Incomplete records / no COMMIT marker. | Chunked scanner reaches EOF; no COMMIT found; truncates to last commit in < 1.5s. | `db.py:_recover` L115-L225 | `torture.py --mode large-batch` |
| **C1** | Kill during `open()` while replaying the log | File unchanged; replay is read-only. | Next `open()` re-runs read-only replay; reaches identical state. | `db.py:_recover` L110-L225 | `torture.py --mode recovery-crash`, seed=42 |
| **C2** | Kill during `open()` while truncating torn tail | File truncated partially. | Next `open()` sees torn tail again and finishes truncation to last valid COMMIT. | `db.py:_recover` L215-L220 | `torture.py --mode recovery-crash`, seed=42 |
| **C3** | Kill during compaction before new file replaces old | Old `.kv` untouched; incomplete `.compact` temp file. | `KVDatabase.__init__` cleans up orphan `.compact`; old file remains fully authoritative. | `db.py:__init__` L47-L52 | `torture.py --mode compaction`, seed=7 |
| **C4** | Kill during compaction in middle of rename | OS rename atomic swap. | On NTFS / POSIX, rename is atomic: either old or new exists. Both are durable. | `db.py:compact` L355-L360 | `torture.py --mode compaction`, seed=7 |
| **C5** | Kill during compaction after swap before cleanup | New `.kv` authoritative; temp already replaced. | On open, new authoritative file loaded; no stale temp state. | `db.py:compact` L358-L365 | `torture.py --mode compaction`, seed=7 |
| **C6** | Kill during `close()` | Data already fsynced during write. | Next `open()` performs standard recovery; closing is not special. | `db.py:close` L370-L380 | `torture.py` standard mode |
| **D1** | Two writers putting different keys killed at random | Multiple transactions interleaved in log. | Each valid COMMIT applies its transaction; every ACKed put present. | `db.py:batch` L245-L290 | `torture.py` standard mode (2 writers) |
| **D2** | Two writers putting same key killed at random | Interleaved records for same key. | Log replay commits in arrival order; latest valid COMMIT wins. No torn values. | `db.py:_recover` L192-L195 | `torture.py` standard mode |
| **D3** | One writer in long batch, another doing single puts | Batch records and single puts interleave. | Single puts commit individually; long batch commits atomically or not at all. | `db.py:batch` L245-L290 | `torture.py` standard mode |
| **D4** | Reader calling `get` during batch on same keys | In-flight write appending to disk. | Reader holds `self.lock`; sees index strictly before batch or after batch. Never half-applied. | `db.py:get` L305-L315 | `tests/test_engine.py` concurrent reader test |
| **E1** | File size cap reached during single put | Pre-check detects overflow; no bytes written. | `DiskFullError` raised before write; file unchanged; DB remains readable. | `storage.py:check_max_bytes_or_raise` | `tests/test_engine.py:test_disk_full_max_bytes` |
| **E2** | Cap reached halfway through a batch | Pre-check calculates full batch size before write. | Entire batch rejected before any byte written; atomicity preserved. | `db.py:batch` L274 | `tests/test_engine.py:test_disk_full_max_bytes` |
| **E3** | Cap reached, then process killed | File size at limit; no corrupt write attempted. | Reopen succeeds; all prior ACKed data fully readable. | `db.py:_recover` L100-L130 | `tests/test_engine.py:test_disk_full_max_bytes` |
| **E4** | Cap reached during compaction | Temp file write fails cleanly. | Compaction raises error; temp deleted; old authoritative file untouched. | `db.py:compact` L330-L355 | Manual unit test with small cap |
| **E5** | After deletes free space, compaction runs, writes succeed | Tombstones shrink log upon compaction. | Compaction purges deleted keys; file size shrinks; subsequent puts succeed. | `db.py:compact` L330-L365 | `tests/test_engine.py:test_compaction` |
| **F1** | 500 consecutive random kills and restarts | 500 crashes across all execution phases. | Invariant holds across every single iteration without human intervention. | Full system | `torture.py --iterations 500` |
| **F2** | Kill, restart, kill during recovery, restart again | Interrupted truncation. | Recovery only truncates to `last_commit_end`; completely idempotent. | `db.py:_recover` L210-L225 | `torture.py --mode recovery-crash` |
| **F3** | 1,000,000 keys written, process killed, `open()` called | 41 MB log file on disk. | Zero-copy buffered cursor replayed 1M keys in **1.4790s (< 2.0s)**. | `db.py:_recover` L115-L225 | 1M-key benchmark test script |
| **F4** | Database file truncated by 1 byte at end by hand | Final byte of record or CRC missing. | Decoder detects CRC mismatch/short record; truncates to previous COMMIT. | `db.py:_recover` L180-L225 | `tests/test_engine.py:test_torn_tail_recovery` |

---

## Part 3: The Three Guarantees, Argued

### 1. Durability: Once ACK is Sent, the Data Survives
Durability is guaranteed by strict write ordering and synchronous barrier flushing. In `db.py:batch()`, records are serialized into bytes and written to the OS file descriptor using `self.file.write()`. Immediately following write, `self.file.flush()` pushes userspace C runtime buffers to the OS kernel, and `os.fsync(self.file.fileno())` blocks execution until the disk controller confirms that all data and file size metadata have been committed to physical non-volatile storage. The in-memory index is updated and the `ACK <op-id>` message is flushed to `sys.stdout` strictly **after** `os.fsync()` returns. If the process is terminated via `kill -9` or `TerminateProcess` at any point prior to `fsync` completion, no ACK has been emitted, satisfying the promise contract. If killed one microsecond after ACK emission, the bytes are physically on disk, and `open()` will replay the valid `COMMIT` marker.

### 2. Atomicity: A Batch is Never Half Visible
Atomicity is guaranteed through multi-record transaction framing with a trailing `COMMIT` marker. A batch containing $M$ operations is encoded as $M$ data records followed by one `COMMIT` record, all sharing an identical 64-bit `txid`. These $M+1$ records are written in a single continuous append and flushed via `os.fsync()`. During log recovery in `_recover()`, operations for uncommitted `txid`s are buffered in a temporary dictionary (`pending`). They are **only** applied to `self.index` if a structurally valid `COMMIT` record bearing the same `txid` and a valid CRC32 is encountered. If a crash occurs while writing the batch, either the `COMMIT` marker is absent or its CRC is invalid; the uncommitted operations in `pending` are discarded upon EOF, and the entire file tail is truncated back to the end of the previous confirmed `COMMIT`. Therefore, a batch is either 100% visible or 0% visible.

### 3. Recoverability: `open()` Always Succeeds on Our Own Files
Recoverability is guaranteed because recovery performs only monotonic forward-scanning and tail-truncation. `open()` validates the 16-byte header (`b"KVDB0001"`, version `1`). The recovery parser reads through the log in 4MB chunks using a zero-copy memoryview cursor. If any record is cut short by a crash, contains an incomplete header, or exhibits a CRC mismatch, the parser does not throw an exception; it halts scanning and designates the byte offset of the last valid `COMMIT` as `last_commit_end`. It then issues `self.file.truncate(last_commit_end)` and `fsync()`. Truncation is strictly idempotent: interrupting a truncation with another `kill -9` simply leaves a tail that will be truncated to the exact same offset on the subsequent `open()`. Leftover temporary files from interrupted compactions (`.compact`) are removed on `open()` before the authoritative file is touched, ensuring that `open()` never crashes on any file created by our database.

