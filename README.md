# Synterra KVDB - "Kill It and Prove It"

A crash-proof, single-file embedded key-value database built from scratch in Python 3 standard library for Synterra's 24-hour engineering hackathon. Survives `kill -9` (`TerminateProcess` on Windows) at any arbitrary machine instruction.

---

## 1. Build and Setup

Synterra KVDB requires **zero external dependencies** (Python 3.8+ standard library only).

### Clean Machine Setup:
```bash
# Verify Python 3 is installed
python --version

# Launch the interactive Terminal UI (default when run without arguments):
python kvdb.py

# Or run CLI directly on Windows:
kvdb.bat open test.kv

# On Linux / macOS:
chmod +x kvdb torture
./kvdb open test.kv
```

---

## 2. Command-Line Interface (All 8 Mandatory Commands)

You can run the engine directly from the repository root using `./kvdb` (or `kvdb.bat` on Windows, or `python -m src.cli`):

### 1. `open`
Opens or creates the database, replays log recovery, and prints key count and elapsed recovery time.
```bash
./kvdb open database.kv [--max-bytes 50000000]
# Output: OK keys=1000000 recovery_time=0.46s
```

### 2. `put`
Stores a key-value pair durably. Returns only after physical `fsync`.
```bash
./kvdb put database.kv user:101 '{"name":"Alice","tier":"gold"}'
# Output: ACK
```

### 3. `get`
Retrieves the value for a key. Returns `NOTFOUND` if missing or deleted.
```bash
./kvdb get database.kv user:101
# Output: {"name":"Alice","tier":"gold"}

./kvdb get database.kv missing_key
# Output: NOTFOUND
```

### 4. `del`
Deletes a key atomically and durably. Deleting a non-existent key is a safe no-op.
```bash
./kvdb del database.kv user:101
# Output: ACK
```

### 5. `batch`
Applies a file of operations atomically. Format: one operation per line (`put <key> <val>` or `del <key>`).
```bash
./kvdb batch database.kv batch_ops.txt
# Output: ACK
```

### 6. `dump`
Outputs all live keys and values in the database sorted lexicographically by key.
```bash
./kvdb dump database.kv
# Output:
# apple red
# banana yellow
# cherry red
```

### 7. `check`
Verifies structural log integrity, validates headers, and reports active key count.
```bash
./kvdb check database.kv
# Output: OK: 3 keys
```

### 8. `serve`
Long-running daemon mode that reads instructions from `stdin` and writes `ACK <op-id>` to `stdout` strictly after physical `fsync`. Used by the torture tool and live demo crash testing.
```bash
./kvdb serve database.kv
# Input:  op101 put greeting hello
# Output: ACK op101
```

### 9. `compact` (Maintenance)
Triggers crash-safe copy-on-write compaction, purging deleted/overwritten keys and reclaiming disk space.
```bash
./kvdb compact database.kv
# Output: OK
```

---

## 3. On-Disk Binary Format

The database lives in **exactly one authoritative file** (`<path>.kv`). It is framed with little-endian integers and protected by 32-bit CRC checksums.

### File Header (16 Bytes)
```
+-----------------------------------+--------------------+--------------------+
| Magic: b"KVDB0001" (8 Bytes)      | Version: u32 (4B)  | Reserved: u32 (4B) |
+-----------------------------------+--------------------+--------------------+
| 0                               8 | 8               12 | 12              16 |
+-----------------------------------+--------------------+--------------------+
```

### Record Format (Little-Endian)
Every log entry consists of a 19-byte fixed header, a variable payload, and a 4-byte CRC32 trailer:

```
+--------------------+------------+--------------------+----------------+----------------+---------------------+-------------------+
| rec_len: u32 (4B)  | type: u8   | txid: u64 (8B)     | klen: u16 (2B) | vlen: u32 (4B) | Payload (klen+vlen) | CRC32: u32 (4B)   |
+--------------------+------------+--------------------+----------------+----------------+---------------------+-------------------+
|<------------------------- Header = 19 Bytes ------------------------->|                |<--- Trailer (4B)->|
```

- **rec_len (4B)**: Total record size in bytes (`19 + klen + vlen + 4`).
- **type (1B)**: `1 = PUT`, `2 = DELETE`, `3 = COMMIT`.
- **txid (8B)**: 64-bit random transaction identifier.
- **klen (2B)**: Key length in bytes (1 to 1,024 bytes).
- **vlen (4B)**: Value length in bytes (0 to 1 MB).
- **Payload**: Raw key bytes followed by raw value bytes.
- **CRC32 (4B)**: Standard IEEE 802.3 CRC32 calculated over all preceding bytes in the record: `zlib.crc32(rec_len..value) & 0xffffffff`.

### Commit Marker Record (23 Bytes)
A transaction commit marker is a standard record with `type = 3`, `klen = 0`, and `vlen = 0`:
```
[rec_len = 23 (4B)][type = 3 (1B)][txid (8B)][klen = 0 (2B)][vlen = 0 (4B)][CRC32 (4B)]
```

---

## 4. Recovery Algorithm in Plain English

When `open()` is called:

1. **Clean Stale Compactions**: Checks if a `<path>.compact` file exists from an interrupted compaction. If present, removes it; the main `.kv` file remains authoritative.
2. **Validate Header**: Reads the first 16 bytes. Confirms `magic == b"KVDB0001"` and `version == 1`. If the file is 0 bytes, writes a fresh 16-byte header and calls `os.fsync()`.
3. **Chunked Memory Scanning**:
   - Reads the log from byte offset 16 forward using 4 MB memory chunks.
   - Parses records using a zero-copy cursor.
   - For every record, verifies that remaining bytes match `rec_len` and validates the CRC32 checksum.
   - If a record is cut short (torn write), corrupted, or has an invalid CRC, the scan **immediately stops**.
4. **Transaction Buffering**:
   - `PUT` and `DELETE` records are placed into an in-memory staging table (`pending[txid]`).
   - When a valid `COMMIT` record is encountered, all staged operations for that `txid` are applied to the in-memory index (`self.index`).
   - The offset of the end of the `COMMIT` record is saved as `last_commit_end`.
5. **Idempotent Tail Truncation**:
   - If the file contains bytes beyond `last_commit_end` (uncommitted records from a killed batch or torn header bytes), the engine calls `self.file.truncate(last_commit_end)` followed by `os.fsync()`.
   - Any uncommitted operations vanish cleanly; acknowledged operations are 100% preserved.

---

## 5. Running the Torture Tool

The torture tool runs concurrent writers, maintenance compaction, and unannounced `kill -9` terminations with an independent Oracle:

```bash
# Standard 500-iteration torture run:
python torture/torture.py --iterations 500 --seed 12345

# Targeted Modes:
python torture/torture.py --iterations 100 --seed 999 --mode large-values
python torture/torture.py --iterations 100 --seed 7 --mode compaction
python torture/torture.py --iterations 100 --seed 42 --mode recovery-crash
python torture/torture.py --iterations 100 --seed 123 --mode large-batch
python torture/torture.py --iterations 10 --seed 3 --mode disk-full --max-bytes 200000
```

---

## 6. Real-World Specialization: Edge Telemetry & Audit Blackbox

To solve real-world industrial storage challenges on embedded edge hardware, KVDB features a first-class domain specialization for **Edge Telemetry & Audit Blackbox Recorders** (e.g., flight data recorders, automotive crash blackboxes, and patient telemetry monitors).

### Why KVDB Fits This Problem:
1. **Harsh Power-Loss Survivability**: Power on edge hardware can be yanked at any millisecond. KVDB guarantees that every acknowledged telemetry frame survives `kill -9` with zero torn writes or corrupted blocks.
2. **Structured Binary Encoders (`src/telemetry.py`)**: High-efficiency binary packing for sensor streams (timestamps, alert levels, metric JSON) packed into the value payload without altering underlying single-file record framing.
3. **Priority Retention Flash Compaction (`./kvdb compact <path> --priority-retention`)**: Onboard flash storage is strictly capped. Under flash capacity limits, KVDB prunes older routine sensor noise while guaranteeing that 100% of emergency event trails (`crit:` prefix) survive permanently.
4. **Cryptographic Attestation Chain (`./kvdb check <path> --audit`)**: Computes a deterministic cumulative SHA-256 Merkle hash chain over all confirmed telemetry for regulatory audit integrity.

### Running the Live Blackbox Crash Demo:
Simulates 50 concurrent high-frequency sensor streams piping into `serve`, abruptly yanks power via `kill -9`, performs post-crash forensic recovery, and verifies zero lost frames:
```bash
python scripts/demo_telemetry.py
```

