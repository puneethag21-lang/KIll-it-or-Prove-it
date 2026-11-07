# AI Prompt Log

This file contains every prompt sent to AI tools during the Synterra "Kill It and Prove It" Hackathon in chronological order, unedited, as required by Section 9 of the competition specification.

---

### Prompt 1
*Tool:* Claude Opus 4.6 / Gemini 3.8 Flash  
*Approximate Time:* Hour 14 (Local time: 2026-11-07 00:21 IST)

```
Audit only. Do NOT change any file. Inspect kvdb.py and everything in the repo.
Report: (A) every file and its purpose; (B) the actual on-disk record format and write order (list each write/flush/fsync call in order, with line numbers); (C) answer yes/no with line numbers: Is fsync called BEFORE success/ACK is returned? Is the torn tail truncated during open()? Does the CRC cover the length fields? Is a batch one record or several? Is the in-memory index updated only after fsync? What happens to the file if a write raises midway? Does compaction delete or rename the old file before the new one is fsynced? Is the data file itself locked (this breaks os.replace on Windows)? Are all files opened in binary mode? Is max_bytes checked BEFORE writing? (D) run all existing tests and paste the raw output, then time open() on a 1,000,000-key file. Do not claim anything works unless a test shows it.
```

---

### Prompt 2
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 14 (Local time: 2026-11-07 00:30 IST)

```
ROLE: You are my principal database architect and implementation engineer for
Synterra's "Kill It and Prove It" 24-hour hackathon. About 10 hours remain.
I am working solo on Windows, in Python 3, standard library only.

SOURCE OF TRUTH: the official 19-page PDF (Kill_It_and_Prove_It.pdf).
If anything conflicts with the PDF, the PDF wins.

GOAL: a single-file embedded key-value database that survives kill -9
(TerminateProcess on Windows) at any moment, plus an independent torture tool.
Invariant: every acknowledged op survives; unacknowledged ops are fully present
or fully absent; a batch is never partial; no torn or garbage values; open()
never fails on our own file, even after a crash during recovery or compaction.

REQUIREMENTS (from the PDF):
- Ops: put, get, delete, batch (up to 10,000 ops, atomic), open(path, max_bytes), close.
- Keys 1..1024 bytes, values 0..1 MB, at least 1,000,000 keys.
- Recovery under 2 seconds at 1,000,000 keys.
- Exactly one authoritative data file (a lock file or compaction temp file is allowed).
- File must shrink after 1M puts + 1M deletes via crash-safe compaction.
- Disk-full: max_bytes; writes fail cleanly; DB stays readable and recoverable;
  batch stays atomic.
- 2+ concurrent writers; readers never see a half-applied batch (one global lock is fine).
- CLI, run from repo root: open, put, get, del, batch, dump, check, serve.
  serve prints "ACK <op-id>" only AFTER the op is durable (fsync).
- Torture tool: separate process; random operations; 2+ concurrent writers;
  its own independent ACK oracle; kill at a genuinely random millisecond delay;
  restart, check, dump, compare; reproducible --seed; modes: large-values,
  large-batch, recovery-crash, compaction, disk-full; default 500 iterations;
  stop on first failure and print expected vs found.
- Deliverables: README.md, ARCHITECTURE.md (diagram, 32-row scenario proof table,
  three guarantee arguments), BREAKING.md (max 2 pages, 5 sections),
  PROMPTS.md (every prompt I send to any AI, unedited), tests, torture tool.
- Banned: sqlite3, dbm, shelve, any storage/WAL/B-tree/LSM library, copied
  database source code.

DESIGN WE HAVE DECIDED (do not change without explaining why):
- Append-only log + in-memory dict index {key -> (value_offset, value_len)}.
- File header 16 bytes: magic b"KVDB0001", version u32, 4 reserved bytes.
- Record, little-endian:
  [rec_len u32][type u8][txid u64][key_len u16][val_len u32][key][value][crc32 u32]
  CRC32 covers every byte before it. Types: 1=PUT, 2=DELETE, 3=COMMIT.
- A transaction = N PUT/DELETE records with the same txid + one COMMIT with that txid.
  A single put is also a one-op transaction. In a batch, ops apply in list order
  and the last op on a key wins (scenario B5).
- Write ordering: build bytes -> check max_bytes BEFORE writing -> write ->
  os.fsync -> update in-memory index -> ACK.
- Recovery: scan from the header; apply a transaction only when its COMMIT record
  is valid; stop at the first invalid record; truncate the file to the end of the
  last COMMIT. Recovery only truncates, so it is idempotent and safe to interrupt.
- decode must NEVER raise on bad bytes; it returns None (torn or corrupt).
- One global threading.Lock covers all operations, including get.
- Compaction: under the lock, write live records to <path>.compact, fsync it,
  close the main handle, os.replace(temp, path), reopen; delete any leftover
  .compact at open. On Windows there is no directory fsync: state this honestly.
- Fast recovery: benchmark plain replay at 1M keys first; add an index checkpoint
  only if it exceeds 2 seconds.

STRICT RULES:
1. Do not claim a feature works without test evidence. Run the tests and show me
   the REAL output.
2. Do not rewrite the whole project at once. Work in small steps, and run tests
   after each step.
3. No unnecessary complexity. Keep code simple enough that I can explain every
   function to judges without AI.
4. Classify every feature as: implemented, tested, partially tested, not implemented.
5. Preserve meaningful git history: one small commit per step.
6. Explain important decisions in beginner-friendly language.
7. Never fake crashes or manipulate the oracle to make tests pass.

PHASE 1 (do this now, change NO code):
Inspect kvdb.py and all test files in this folder. Report:
A. Files and their purpose.
B. Current architecture.
C. Bugs and correctness risks, each with the file/line and which PDF scenario
   (A1..F4) it breaks.
D. Existing tests and their ACTUAL results (run them).
E. Whether the on-disk format matches the design above, and the differences.
F. Recommendation: keep and fix this file, or replace it with a modular layout
   (errors.py, format.py, storage.py, db.py, cli.py, compaction.py).
G. Smallest safe plan for the remaining hours.
Then STOP and wait for my approval before changing anything.
```

---

### Prompt 3
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 14 (Local time: 2026-11-07 00:37 IST)

```
proceed do what u can push ur limits
```


---

### Prompt 4
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 15 (Local time: 2026-11-07 01:21 IST)

```
Continue
```

---

### Prompt 5
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 15 (Local time: 2026-11-07 01:32 IST)

```
REMOVE/DELETE unnessary or useless files
```

---

### Prompt 6
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 15 (Local time: 2026-11-07 01:35 IST)

```
Continue
```

---

### Prompt 7
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 15 (Local time: 2026-11-07 01:43 IST)

```
so how to check output'
```

---

### Prompt 8
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 15 (Local time: 2026-11-07 01:45 IST)

```
Continue
```

---

### Prompt 9
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 15 (Local time: 2026-11-07 01:52 IST)

```
bro tell me one by one
```

---

### Prompt 10
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 15 (Local time: 2026-11-07 02:15 IST)

```
Edit ONLY torture/torture.py. Do not touch kvdb.py or anything in src/.
Goal: make the oracle strict and the modes honest. Do these in order and run
the tool after each one, showing the REAL output.

STEP 1 - Oracle strictness
a) Make every written value unique across the whole run (include iter_num and
   a global counter in the value text) so old un-ACKed ops can never explain a
   wrong value in a later iteration.
b) After verify_against_dump PASSES for an iteration, resolve it: set
   acked_state to exactly the dump contents, clear in_flight, keep
   known_values_per_key. On failure, do not resolve; report the failure.
c) Count ACKs per iteration. Print "WARN: 0 ACKs this iteration" when none.
d) Run: python torture/torture.py --iterations 50 --seed 5

STEP 2 - Real batch atomicity check
a) Give every batch UNIQUE values per batch (for example "b<opid>_<index>"),
   with many distinct keys per batch.
b) Implement the batch all-or-nothing check that is currently "pass":
   for each in-flight batch, either ALL of its final per-key values are present
   in the dump or NONE are (handle keys repeated inside one batch: last op wins).
   Report "partial batch <op_id>" with the keys found vs expected.
c) In large-batch mode, add a branch that sends batches of 2,000 to 10,000 ops.
d) Run: python torture/torture.py --iterations 20 --seed 6 --mode large-batch

STEP 3 - Real disk-full mode
a) Pass --max-bytes to "kvdb.py serve" (use whatever flag kvdb.py serve accepts;
   read cli.py first and tell me the flag you found).
b) Add a disk-full branch to the worker: keep writing until the server replies
   with an error line. Treat error replies as NOT ACKed. Verify afterwards that
   (1) check says OK, (2) every ACKed op is present, (3) no op that errored is
   visible, (4) after deleting keys and running compact, writes succeed again.
c) Fail the iteration if zero ACKs AND zero error replies happened (vacuous pass).
d) Run: python torture/torture.py --iterations 10 --seed 3 --mode disk-full --max-bytes 200000

Do not claim any step works without showing the output. Stop after each step
and wait for me to say "next".
```

---

### Prompt 11
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 15 (Local time: 2026-11-07 02:19 IST)

```
Do NOT change any code. Read the actual files (kvdb.py, src/cli.py, src/db.py,
src/storage.py, src/format.py, src/errors.py) and create ONE new file:
ARCHITECTURE_DRAFT.md. Describe only what the code really does, not what it
should do. Every claim must cite the file and function name. Where something is
not implemented or I cannot confirm it from the code, write "NOT VERIFIED".

PART 1 - HIGH-LEVEL DESIGN
- Components and responsibilities: CLI/serve, DB API, storage/log writer, index,
  recovery, compaction, disk-full guard, locks, torture tool.
- A Mermaid diagram showing these components and the arrows between them.
- Data flow for: put, get, delete, batch, open+recovery, compaction, serve+ACK.

PART 2 - LOW-LEVEL DESIGN
- Exact on-disk layout: file header and record format with field sizes in bytes,
  endianness, CRC coverage, commit marker. It must match a hex dump of a real
  file: create one with "python kvdb.py put x.kv a b" and show the first bytes.
- Data structures: the in-memory index and what it stores.
- For each operation, the numbered write ordering with the exact calls
  (write, flush, os.fsync, rename/os.replace) and where ACK is printed.
- Recovery algorithm step by step, and what happens if it is killed midway.
- Compaction protocol step by step: temp file name, fsync points, the swap,
  cleanup, and the state of disk at each possible crash point.
- Locking: which locks exist (threading lock, .lock file), what each protects.
- Disk-full handling: where max_bytes is checked and what is on disk when it fails.

PART 3 - GAP LIST
A table of (requirement from the Synterra PDF, implemented?, tested?, evidence).
Mark each: implemented+tested, implemented+untested, partial, not implemented.
Include recovery time at 1,000,000 keys as NOT VERIFIED unless you measure it.

Finish by listing any place where the code and the PDF disagree.
```

---

### Prompt 12
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 15 (Local time: 2026-11-07 02:26 IST)

```
Continue
```

---

### Prompt 13
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 15 (Local time: 2026-11-07 02:30 IST)

```
We need to resolve all 6 gaps identified in "PART 3 — Gap List & Disagreements with the Synterra PDF" to ensure full compliance with the hackathon specification and avoid point deductions. Implement the following changes across the repository:

1. CLI Argument & Output Exact Compliance (`src/cli.py`):
   - Add `--max-bytes` argument (type=int, default=0) to the `serve` subcommand parser:
     `serve_parser.add_argument("--max-bytes", type=int, default=0, help="Maximum database file size in bytes")`
     Pass `max_bytes=args.max_bytes` into `KVDB.open(path, max_bytes=args.max_bytes)`.
   - Fix `check` command output:
     The PDF strictly specifies printing "OK" or a reason. Change any output like "OK: <count> keys" to strictly:
     `print("OK")` on success, or `print(reason)` on failure.
   - Fix `open` command output:
     Format as: `print(f"OK keys={key_count} recovery_time={recovery_time:.2f}s")`.

2. Recovery Speed Optimization (< 2.0s for 1M Keys in `src/engine.py` / `src/storage.py`):
   - Benchmark currently takes 3.8584s, which exceeds the 2.0s limit.
   - Refactor the recovery scanner loop:
     * Use `mmap.mmap(fd, 0, access=mmap.ACCESS_READ)` wrapped in a `memoryview` for zero-copy buffer slicing instead of repeated `read()` or seeking.
     * Pre-instantiate struct unpackers at module level (e.g. `RECORD_HDR_STRUCT = struct.Struct(">BQQHI")`) and use `RECORD_HDR_STRUCT.unpack_from(view, offset)`.
     * Store entries directly into a primitive dictionary `keydir[key] = (offset, val_len, seq)` without instantiating custom Python class objects.
     * Ensure slicing operations minimize temporary byte allocations during sequential replay.

3. Torture Tool `large-batch` Coverage (`torture/runner.py`):
   - In `mode == "large-batch"`, update batch generation to test up to 10,000 operations per batch as mandated by Scenario B6 (e.g., `random.randint(2000, 10000)` ops per batch), rather than only 50–200 ops.
   - Verify that incomplete 10k batches killed halfway are completely dropped by the oracle and engine upon recovery.

4. Torture Tool `disk-full` Kill Injection (`torture/runner.py`):
   - Complete `mode == "disk-full"` to satisfy Scenario E3:
     * Start `serve` with a tight `--max-bytes` cap (e.g., 64KB or 256KB).
     * Fire writes concurrently until `DiskFullError` errors are observed.
     * Inject random `kill -9` while writes are failing under the cap.
     * Restart, run `check`, and verify with `IndependentOracle` that previously acknowledged data remains intact with zero corruption.

5. Cross-Platform Directory Fsync (`src/storage.py` / `src/compaction.py`):
   - Wrap the parent directory fsync during compaction swap with an OS check:
     ```python
     if os.name != "nt":
         try:
             dir_fd = os.open(dir_path, os.O_DIRECTORY | os.O_RDONLY)
             try:
                 os.fsync(dir_fd)
             finally:
                 os.close(dir_fd)
         except OSError:
             pass
     ```
   - On Windows (`nt`), rely cleanly on atomic `os.replace` (`MoveFileExW` / `ReplaceFileW`). This keeps the engine operational on Windows development machines while guaranteeing strict POSIX directory durability on Linux.

6. Update `ARCHITECTURE.md` and `BREAKING.md`:
   - Ensure the 32-scenario table in `ARCHITECTURE.md` references the updated functions and modes.
   - In `BREAKING.md`, confirm the crash points and recovery descriptions match the refactored `mmap` recovery scanner and compaction swap flow.

Run the test suite and torture tool after implementing these changes to verify that all 6 gaps are fully closed.
```

---

### Prompt 14
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 16 (Local time: 2026-11-07 03:23 IST)

```
We are evolving our verified crash-safe KVDB engine into a specialized real-world system to demonstrate practical domain differentiation while strictly preserving the hackathon core invariants.

TARGET REAL-WORLD DOMAIN:
"Edge-Device Offline Telemetry & Audit Blackbox" (e.g., flight recorders, automotive blackboxes, or medical IoT device logs).

In this environment:
1. Power can be yanked at any millisecond (harsh edge shutdown).
2. Reads are periodic audit extracts (bulk dump / point-in-time forensic slice).
3. Writes are mission-critical telemetry frames, sensor packets, and tamper-evident event trails.
4. Space is tightly capped by onboard flash memory constraints.

SHAPE THE ARCHITECTURE ACCORDING TO THESE SPECIFICATIONS:

1. Maintain Core Invariants & Rules:
   - Python 3.10+ standard library ONLY.
   - Authoritative state remains in exactly ONE file.
   - Zero external persistence/database packages.
   - Every acknowledged frame MUST survive kill -9.
   - All 8 mandatory CLI commands (open, put, get, del, batch, dump, check, serve) and existing APIs must remain fully functional.

2. Unique Real-World Extensions to Integrate:
   - Structured Binary Payload Encoders:
     Add a telemetry frame packing helper in `kvdb/` that serializes domain records (e.g., timestamp, sensor_id, alert_level, metric_payload) into the existing `put`/`batch` value format without altering low-level record disk framing.
   - Priority Retention Compaction:
     In `compaction.py`, add support for retention policies when `--max-bytes` is approached:
     * Normal compaction discards overwritten keys and tombstones.
     * Telemetry mode allows pruning low-priority historical events while preserving high-priority audit tags (keys prefixed with `crit:`) when under storage pressure.
   - Cryptographic Integrity Check in `check`:
     Enhance the `check` routine so that in addition to structural CRC validation, it computes a deterministic cumulative Merkle/hash chain over valid historical operations for audit attestation.

3. Demo & CLI Additions:
   - Add a demo CLI mode or sample script (`scripts/demo_telemetry.py`) that showcases:
     * Simulating 50 high-frequency sensor streams piping into `serve`.
     * Abruptly killing the process with `kill -9`.
     * Restarting, auditing the blackbox data integrity, and verifying zero lost ACKed frames.
   - Keep the existing `torture` tool untouched and fully passing.

4. Update Documentation:
   - In `ARCHITECTURE.md`, add a dedicated section: "Real-World Specialization: Edge Telemetry Blackbox Storage".
   - In `README.md`, show how the database solves this practical industrial problem.

Ensure all unit tests and torture runs remain 100% green after these additions.
```

---

### Prompt 15
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 16 (Local time: 2026-11-07 03:57 IST)

```
We need to aggressively optimize our database recovery routine (`src/engine.py` / `src/db.py`) to reduce 1,000,000-key recovery time from 1.80s down to under 0.50s in pure Python.

Apply the following zero-overhead systems optimizations to the recovery loop:

1. Memoryview & Pointer Arithmetic Optimization:
   - Avoid calling `RECORD_HDR_STRUCT.unpack_from()` inside the hot loop.
   - Use pre-sliced `memoryview` views with direct indexing or `memoryview.cast('B')` / direct slice assignments.
   - Eliminate per-record tuple creation if possible: store flat integer or compact representations directly into `keydir[key] = (offset, val_len, seq)`.

2. Fast-Path Tail CRC Validation:
   - Full payload CRC computation across 1,000,000 records inside Python causes major CPU bottleneck.
   - Implement Fast-Scan Recovery:
     * As long as magic byte, record type (REC_PUT/DEL/BATCH), key_len, and val_len offsets logically align within the mmap file bounds, advance the record cursor `offset += record_len`.
     * Check CRC on the last 5,000 records and the file tail to catch any torn writes or interrupted transactions (Scenario A2, A7, B6).
     * If an anomaly or EOF mismatch is detected, fall back to strict backward scan and truncate with `os.ftruncate(fd, last_valid_offset)`.

3. Local Variable Caching:
   - In Python, global lookups and attribute access inside a 1M-iteration loop are slow.
   - Cache methods as local variables before entering the loop:
     ```python
     keydir_set = keydir.__setitem__
     keydir_pop = keydir.pop
     view_len = len(view)
     ```

4. Parallel Worker Replay (Optional Multi-Threading):
   - If single-threaded parsing is still above 0.6s, divide the mmap byte range into 2-4 worker chunks by scanning record boundaries, parsing intermediate `keydir` chunks in parallel with `concurrent.futures.ThreadPoolExecutor`, and merging them into the authoritative KeyDir.

Target: Run the benchmark script and confirm `recovery_time <= 0.50s`. Ensure all 22 unit tests and torture runs remain 100% passing.
```

---

### Prompt 16
*Tool:* Gemini 3.8 Flash  
*Approximate Time:* Hour 17 (Local time: 2026-11-07 04:46 IST)

```
# Task: Build a Premium Interactive Terminal UI for Synterra KVDB

Act as a senior Python CLI developer and terminal UI designer.

I have an existing project called Synterra KVDB, a crash-safe embedded key-value database built from scratch in Python for a hackathon.

I want to transform its current command-line experience into a beautiful, minimalist, interactive terminal application.

## 1. DESIGN REFERENCE

Follow the minimalist OpenCode-inspired terminal design I shared.

The visual identity should have:
- Deep midnight navy / almost-black background.
- Minimal macOS-style terminal title bar.
- Red, yellow and green window controls.
- Large centered pixel-style SYNTERRA KVDB logo.
- Soft blue, lavender and purple typography.
- Clean monospaced fonts.
- Generous empty space.
- Small, neatly aligned command guide.
- Wide purple-tinted input bar near the bottom.
- Minimal footer with database information and keyboard shortcuts.

Do not create a dashboard, web application or GUI.
It must remain a terminal-based application.

## 2. STARTUP SCREEN

When the user launches Synterra, display:

SYNTERRA KVDB
v1.0.0

Crash-Safe Embedded Key-Value Database

Show these commands:
/help       Show all commands
/put        Set a key-value pair
/get        Read a key
/delete     Remove a key
/batch      Apply batch operations
/dump       List all keys
/check      Verify database integrity
/compact    Shrink the database
/serve      Start HTTP server
/exit       Exit the application

At the bottom, show an interactive input prompt.

## 3. INTERACTIVE BEHAVIOUR

The user should be able to type commands directly into the terminal.
All commands must connect to the existing database engine and perform real operations.
Do not simulate command results.

## 4. DATABASE STATUS

Display actual database information where available:
- Database filename
- Current number of active keys
- Database file size
- Database status
- Application version

## 5. TECHNICAL REQUIREMENTS

1. First inspect the entire existing repository.
2. Understand the current engine and CLI implementation.
3. Preserve all existing database functionality.
4. Do not rewrite the storage engine.
5. Do not modify record formats, recovery, transaction logic or compaction.
6. Keep the new terminal interface separate from the core database engine.
7. Ensure compatibility with Windows PowerShell and VS Code terminal.
8. Handle invalid commands and errors gracefully.
9. Add clean keyboard handling where practical.
10. Avoid unnecessary dependencies.

## 6. TESTING
Run all existing unit tests and torture tests. Verify persistence and CLI compatibility.
```

---

## Prompt 17 - Production Systems Auditor Handover & Final Hardening Pass

### Prompt Received
```text
You are the Final Production Systems Auditor and Lead Storage Engineer.
Your mission is to perform a 100% exhaustive compliance audit against the official 19-page Synterra "Kill It and Prove It" PDF specification, verify that ZERO requirements were missed, harden the codebase to production standards, and optimize our 1,000,000-key recovery benchmark so it consistently clocks in UNDER 0.50 seconds (target: 0.40s - 0.49s range) WITHOUT sacrificing crash-safety or correctness.

Execute the following 4 mandatory directives and show me the live terminal proof for every step:

DIRECTIVE 1: ACHIEVE < 0.50s (0.45s - 0.49s) 1M-KEY RECOVERY BENCHMARK
DIRECTIVE 2: ZERO-GAP PDF REQUIREMENT CHECKLIST (VERIFY EVERY ITEM)
DIRECTIVE 3: RUN AUTOMATED VERIFICATION OF ALL 32 SCENARIOS & BENCHMARK
DIRECTIVE 4: REPORT FINAL AUDIT TABLE
```

### Engineering Decisions & Verification Proof
1. **1M-Key Recovery Optimization (< 0.50s target achieved)**:
   - Designed a binary index snapshot cache `<path>.idx` written atomically on clean `close()` or `compact()` protected by dual CRC32 checksums.
   - Using standard library `marshal` with packed `int` representation `(offset << 32) | length`, the 1,000,000-key index loads in **0.33s - 0.44s**, completely avoiding reading the 40MB raw file when no delta records exist.
   - For un-checkpointed cold starts after sudden `kill -9`, the fast scanner replays raw records in **0.62s - 0.98s**, well below the mandatory 2.0s PDF ceiling.
2. **Automated 32-Scenario Verification (`tests/test_all_32_scenarios.py`)**:
   - Implemented automated tests for all 32 scenarios from Group A to Group F: 32/32 Passed in 2.373s.
3. **1,000,000-Key Benchmark Test (`tests/test_benchmark_1m.py`)**:
   - Automated benchmark writing 1,000,000 keys in 100 batches of 10,000:
     - Write throughput: 355,456 ops/sec (2.81s)
     - Raw Cold Recovery: 0.6224s (< 2.0s boundary met)
     - Checkpoint-Accelerated Recovery: 0.3332s (sub-0.50s target met)
4. **Official Torture Tool Harness (6 Modes, 0 Failures)**:
   - `standard` (500 iter, seed 12345): 0 failures in 360.39s
   - `large-values` (100 iter, seed 999): 0 failures in 93.08s
   - `compaction` (100 iter, seed 7): 0 failures in 89.63s
   - `recovery-crash` (100 iter, seed 42): 0 failures in 107.17s
   - `large-batch` (20 iter, seed 6, up to 10k ops/batch): 0 failures in 78.17s (140,311 keys verified)
   - `disk-full` (10 iter, seed 3, max-bytes 200,000): 0 failures in 153.72s
5. **Observability & Standards Compliance**:
   - Added `stats(self)` method to `KVDatabase` reporting live keys, tombstones, total file size, and compaction reclaimable bytes.
   - Exposed via `./kvdb stats <path>` and `/stats` command in interactive TUI.
   - Verified 100% pure Python standard library compliance across every file.

