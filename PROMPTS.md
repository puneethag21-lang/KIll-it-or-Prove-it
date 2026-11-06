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
