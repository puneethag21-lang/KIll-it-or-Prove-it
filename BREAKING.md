# BREAKING.md - "How I Would Break This"

This document provides a critical self-attack on the KVDB engine architecture, detailing exact failure boundaries, durability mechanics, known architectural weaknesses, and lessons learned from crash testing.

---

## 1. Crash Points Table

The following table analyzes critical crash points in the write, recovery, and compaction paths:

| # | Exact Location in Code | What is on Disk at Moment of Kill | What `open()` Does About It |
|---|---|---|---|
| **1** | `db.py:batch` before `file.write()` | Unmodified log file; write buffer only in userspace memory. | Normal recovery; all prior ACKed transactions are replayed; current write is completely absent. |
| **2** | `db.py:batch` mid-way through `file.write()` | Partial bytes of record header or payload appended to end of file. | `_recover()` detects truncated record at EOF (or bad CRC), halts replay, and calls `file.truncate(last_commit_end)`, restoring file to clean state. |
| **3** | `db.py:batch` after `write()` before `flush()` | Dirty pages in Python userspace buffer; nothing guaranteed on physical media. | `open()` either sees the unwritten state or torn tail; recovery truncates to last valid COMMIT. No ACK was sent, so caller is not misled. |
| **4** | `db.py:batch` after `flush()` before `fsync()` | Bytes in OS kernel page cache; physically uncommitted. | Either kernel flushed pages before death (transaction valid and replayed), or bytes lost (tail truncated). Invariant holds. |
| **5** | `db.py:batch` after `fsync()` before index update / ACK | Record + COMMIT marker completely durable on physical disk. | Next `open()` reads valid COMMIT, updates `self.index`, making data visible. The write survived even though caller died before seeing ACK. |
| **6** | `db.py:_recover` during log chunk reading | Authoritative file with existing data; read buffer in memory. | Replay is read-only; subsequent `open()` simply re-reads from byte 0 and completes cleanly. Fully idempotent. |
| **7** | `db.py:_recover` mid-way through `file.truncate()` | File length partially adjusted or metadata uncommitted. | Next `open()` detects remaining torn bytes past `last_commit_end` and re-executes `file.truncate(last_commit_end)`. |
| **8** | `db.py:compact` while writing `<path>.compact` | Original database intact; incomplete `.compact` file on disk. | `__init__` detects leftover `.compact` and removes it with `os.remove()`. Original file remains 100% authoritative. |
| **9** | `db.py:compact` after `fsync(temp)` before `os.replace` | Fully compacted `.compact` durable; old `.kv` still in place. | Old file is authoritative; `__init__` cleans up `.compact` temp file. Zero data loss. |
| **10** | `db.py:compact` during `os.replace(temp, path)` | OS atomic directory entry pointer swap. | File systems (NTFS, ext4) execute rename atomically: either old or new file is authoritative. Both are durable and complete. |
| **11** | `db.py:compact` after `os.replace` before `_recover()` | New compacted file on disk; in-memory index not yet refreshed. | Next `open()` opens new authoritative file and builds index from the compacted state. |
| **12** | `storage.py:check_max_bytes_or_raise` | File at disk cap; write rejected in userspace. | Database file untouched; `open()` succeeds; all existing data intact and readable. |

---

## 2. The Ordering Argument

Our architecture guarantees correctness under unannounced process termination because of strict barrier sequencing:

$$\text{Validate \& MaxBytes Check} \longrightarrow \text{write()} \longrightarrow \text{flush()} \longrightarrow \text{fsync()} \longrightarrow \text{Index Mutation} \longrightarrow \text{ACK}$$

1. **Serialization Before I/O**: All $N$ records of a batch plus the terminating `COMMIT` marker are packed into a single byte stream in memory before invoking I/O.
2. **Atomic Ingestion**: The entire byte sequence is handed to the kernel via a single `self.file.write()` syscall.
3. **Hardware Flush Barrier**: Python's `flush()` flushes userspace buffers, followed immediately by `os.fsync(self.file.fileno())`. On Windows, `os.fsync()` calls the Win32 API `FlushFileBuffers()`; on POSIX, it calls `fsync()`. Both system calls force the storage controller to commit cached dirty blocks and file allocation table/MFT metadata to non-volatile physical storage.
4. **Delayed ACK**: The in-memory index is updated and the `ACK` token is emitted to standard output **only after** `FlushFileBuffers`/`fsync` returns control. If the process dies at any instruction before this point, no ACK was emitted. If it dies after, the data is guaranteed to exist on physical media.
5. **Atomic Swap & Directory Sync**: Compaction writes live records to a temporary file `<path>.compact` and calls `os.fsync()` on the temporary file descriptor. On POSIX systems, `sync_directory_if_posix()` opens the directory via `os.open(dir, os.O_RDONLY)` and calls `os.fsync(dir_fd)` to ensure directory entry durability. On Windows (NTFS), directory fsync is not supported by the kernel; instead, we rely on the documented NTFS transactional metadata journal, which guarantees that `os.replace()` (calling `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`) is an atomic metadata swap.

---

## 3. Known Weaknesses

We acknowledge the following three genuine architectural weaknesses in our design:

1. **Recovery is $O(\text{log size})$ on Uncompacted Logs**:  
   Because we rely on replaying append-only records to reconstruct the in-memory index on `open()`, recovery time is proportional to the total bytes appended since the last compaction. On a 1,000,000-key file, our chunked cursor scanner recovers in 1.48s (under the 2.0s limit). However, if an uncompacted log accumulated 5,000,000 churn operations, replay would take ~7 seconds unless compaction is executed periodically.
2. **Windows Lack of Directory Fsync**:  
   On Windows, NTFS does not expose a directory `fsync` system call. While `os.replace` is atomic across process termination, a hard hardware power loss (pulling the laptop power cord) precisely during `MoveFileExW` could theoretically leave the filesystem journal to roll back to the old file entry. Our design survives `kill -9` / `TerminateProcess` 100% reliably, but full hardware power-loss resilience on Windows is constrained by OS driver design.
3. **Lock Contention with Coarse-Grained Synchronization**:  
   We use an exclusive `threading.RLock()` combined with cross-process `msvcrt.locking(1)` on a separate `.lock` file. While this guarantees 100% safety against concurrent corruption, read operations (`get`) and write operations (`put`, `batch`) serialize on the same lock. Under massive concurrent reader workloads (e.g., 50 threads), readers experience lock queueing.

---

## 4. What We Tried That Failed

1. **Naive Individual Syscalls in Recovery (Exceeded Speed Target)**:  
   Our initial prototype read records using three distinct `read()` calls per entry (`read(21)`, `read(klen+vlen)`, `read(4)`). On 1,000,000 keys, this resulted in 3,000,000 syscalls, taking **2.587 seconds** and failing the 2.0-second limit. We abandoned this in favor of a 4MB chunked memoryview cursor scanner, which slashed recovery time to **1.479 seconds** (a 43% speedup).
2. **Bytearray In-Place Slice Deletion (`BufferError`)**:  
   To manage chunk memory, we attempted `del buf[:cursor]` while maintaining an open `memoryview` on `buf`. Python threw `BufferError: Existing exports of data: object cannot be re-sized`. We refactored the recovery loop to use transient memoryview slices solely within the CRC calculation, releasing buffer locks cleanly.
3. **Premature EOF in Torture Supervisor Output Drain**:  
   Our early torture tool checked a `stop_workers` event inside the ACK listener. When `proc.kill()` fired, the listener exited prematurely, leaving valid ACKs that had already been flushed by `serve` trapped in the OS pipe buffer. The torture tool mistakenly reported missing ACKs. We fixed this by ensuring `ack_listener` drains the pipe until true EOF (`readline() == b""`), properly capturing all durable promises.

---

## 5. If We Had 24 More Hours

1. **Memory-Mapped (mmap) Read Path**:  
   We would implement an `mmap`-backed reader for `get()` operations, allowing concurrent readers to read value bytes directly from shared memory pages without disk seeks or lock contention.
2. **Periodic Checkpoint / Hint Index File**:  
   To handle databases with > 10,000,000 keys, we would persist an immutable checkpoint index file (`.idx`) during compaction, reducing `open()` recovery time from 1.48 seconds to under 0.05 seconds regardless of log length.
3. **Background Auto-Compaction Daemon**:  
   We would implement an asynchronous background compaction thread that triggers copy-on-write compaction whenever deleted or overwritten keys exceed 30% of total log volume, completely automating file shrinkage.

