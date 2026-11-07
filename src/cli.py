"""
Command-Line Interface (CLI) implementation for Synterra's KVDB.
Implements all mandatory commands from Section 6 of the hackathon specification:
  open, put, get, del, batch, dump, check, serve
"""

import sys
import os
import time
import argparse
from typing import List, Tuple, Optional

from src.db import KVDatabase
from src.errors import KVDBError, DiskFullError, CorruptedDatabaseError


def parse_batch_file(filepath: str) -> List[Tuple[str, str, str]]:
    """
    Parses a batch file where each line is:
      put <key> <value>
      del <key>
    """
    ops = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(maxsplit=2)
            action = parts[0].lower()
            if action == "put":
                if len(parts) < 3:
                    raise ValueError(f"Line {line_num}: 'put' requires key and value")
                ops.append(("put", parts[1], parts[2]))
            elif action in ("del", "delete"):
                if len(parts) < 2:
                    raise ValueError(f"Line {line_num}: 'del' requires key")
                ops.append(("del", parts[1], ""))
            else:
                raise ValueError(f"Line {line_num}: Unknown operation '{parts[0]}'")
    return ops


def run_serve(db_path: str, max_bytes: Optional[int] = None):
    """
    Long-running serve mode: reads operations from stdin and prints ACK after fsync.
    Lines accepted:
      <op-id> put <key> <value>
      <op-id> del <key>
      <op-id> batch <file-or-semicolon-separated-ops>
      put <key> <value>
      del <key>
    """
    db = KVDatabase(db_path, max_bytes=max_bytes)
    try:
        # Signal readiness to supervisor
        sys.stdout.write("READY\n")
        sys.stdout.flush()

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            parts = line.split(maxsplit=3)
            if not parts:
                continue

            # Check if first token is an op-id or a command
            first_lower = parts[0].lower()
            if first_lower in ("put", "del", "delete", "batch", "compact"):
                op_id = None
                cmd = first_lower
                cmd_args = parts[1:]
            else:
                op_id = parts[0]
                if len(parts) < 2:
                    continue
                cmd = parts[1].lower()
                cmd_args = parts[2:]

            try:
                if cmd == "put":
                    if len(cmd_args) >= 2:
                        k, v = cmd_args[0], cmd_args[1]
                    elif len(cmd_args) == 1:
                        k, v = cmd_args[0], ""
                    else:
                        continue
                    db.put(k, v)

                elif cmd in ("del", "delete"):
                    if not cmd_args:
                        continue
                    db.delete(cmd_args[0])

                elif cmd == "batch":
                    if not cmd_args:
                        continue
                    batch_arg = cmd_args[0]
                    if os.path.exists(batch_arg):
                        ops = parse_batch_file(batch_arg)
                    else:
                        ops = []
                        raw_ops = " ".join(cmd_args).split(";")
                        for rop in raw_ops:
                            sub_parts = rop.strip().split(maxsplit=2)
                            if not sub_parts:
                                continue
                            sub_cmd = sub_parts[0].lower()
                            if sub_cmd == "put" and len(sub_parts) == 3:
                                ops.append(("put", sub_parts[1], sub_parts[2]))
                            elif sub_cmd in ("del", "delete") and len(sub_parts) >= 2:
                                ops.append(("del", sub_parts[1], ""))
                    if ops:
                        db.batch(ops)

                elif cmd == "compact":
                    if cmd_args and cmd_args[0] in ("--priority", "priority", "telemetry"):
                        db.compact(priority_retention=True)
                    else:
                        db.compact()

                # Print ACK only AFTER durable fsync
                if op_id is not None:
                    sys.stdout.write(f"ACK {op_id}\n")
                else:
                    sys.stdout.write("ACK\n")
                sys.stdout.flush()

            except Exception as e:
                # Output error line to stdout and stderr so caller can detect
                err_msg = f"ERROR: {e}\n"
                sys.stderr.write(err_msg)
                sys.stderr.flush()
                sys.stdout.write(f"ERR {op_id if op_id else ''} {e}\n")
                sys.stdout.flush()

    finally:
        db.close()


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    # Launch interactive Terminal UI by default when executed without arguments
    if len(argv) == 0:
        from src.tui import run_tui
        run_tui()
        return

    parser = argparse.ArgumentParser(
        prog="kvdb",
        description="Synterra Crash-Safe Key-Value Database"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 0. ui [path]
    p_ui = subparsers.add_parser("ui", help="Launch interactive Terminal UI")
    p_ui.add_argument("path", nargs="?", default="synterra.kv", help="Database file path")

    # 1. open <path> [--max-bytes N]
    p_open = subparsers.add_parser("open", help="Open or create DB, run recovery, print key count & time")
    p_open.add_argument("path", help="Database file path")
    p_open.add_argument("--max-bytes", type=int, default=0, help="Maximum allowable database file size")

    # 2. put <path> <key> <value>
    p_put = subparsers.add_parser("put", help="Store key-value pair")
    p_put.add_argument("path", help="Database file path")
    p_put.add_argument("key", help="Key")
    p_put.add_argument("value", help="Value")

    # 3. get <path> <key>
    p_get = subparsers.add_parser("get", help="Retrieve value by key")
    p_get.add_argument("path", help="Database file path")
    p_get.add_argument("key", help="Key")

    # 4. del <path> <key>
    p_del = subparsers.add_parser("del", help="Delete key")
    p_del.add_argument("path", help="Database file path")
    p_del.add_argument("key", help="Key")

    # 5. batch <path> <file>
    p_batch = subparsers.add_parser("batch", help="Apply batch of operations from file")
    p_batch.add_argument("path", help="Database file path")
    p_batch.add_argument("file", help="File containing batch operations")

    # 6. dump <path>
    p_dump = subparsers.add_parser("dump", help="Print all keys and values sorted by key")
    p_dump.add_argument("path", help="Database file path")

    # 7. check <path> [--audit]
    p_check = subparsers.add_parser("check", help="Verify database integrity")
    p_check.add_argument("path", help="Database file path")
    p_check.add_argument("--audit", "--attest", action="store_true", help="Output cryptographic audit attestation hash")

    # 8. serve <path> [--max-bytes N]
    p_serve = subparsers.add_parser("serve", help="Long-running mode: read ops from stdin, print ACK")
    p_serve.add_argument("path", help="Database file path")
    p_serve.add_argument("--max-bytes", type=int, default=0, help="Maximum database file size in bytes")

    # Optional utility: compact <path> [--priority-retention] [--target-bytes N]
    p_compact = subparsers.add_parser("compact", help="Trigger crash-safe compaction")
    p_compact.add_argument("path", help="Database file path")
    p_compact.add_argument("--priority-retention", action="store_true", help="Preserve crit: tags while pruning older telemetry")
    p_compact.add_argument("--target-bytes", type=int, default=None, help="Target storage budget in bytes")

    # Observability: stats <path>
    p_stats = subparsers.add_parser("stats", help="Show database storage statistics")
    p_stats.add_argument("path", help="Database file path")

    args = parser.parse_args(argv)

    if args.command == "open":
        max_bytes = args.max_bytes if args.max_bytes > 0 else None
        t0 = time.perf_counter()
        db = KVDatabase(args.path, max_bytes=max_bytes)
        t1 = time.perf_counter()
        key_count = db.check()
        recovery_time = t1 - t0
        db.close()
        print(f"OK keys={key_count} recovery_time={recovery_time:.2f}s")

    elif args.command == "put":
        db = KVDatabase(args.path)
        try:
            db.put(args.key, args.value)
            print("ACK")
        finally:
            db.close()

    elif args.command == "get":
        db = KVDatabase(args.path)
        try:
            val = db.get(args.key)
            if val is None:
                print("NOTFOUND")
            else:
                try:
                    print(val.decode("utf-8"))
                except UnicodeDecodeError:
                    print(val.hex())
        finally:
            db.close()

    elif args.command == "del":
        db = KVDatabase(args.path)
        try:
            db.delete(args.key)
            print("ACK")
        finally:
            db.close()

    elif args.command == "batch":
        ops = parse_batch_file(args.file)
        db = KVDatabase(args.path)
        try:
            db.batch(ops)
            print("ACK")
        finally:
            db.close()

    elif args.command == "dump":
        db = KVDatabase(args.path)
        try:
            items = db.dump()
            for k, v in items:
                try:
                    k_str = k.decode("utf-8")
                except UnicodeDecodeError:
                    k_str = k.hex()
                try:
                    v_str = v.decode("utf-8")
                except UnicodeDecodeError:
                    v_str = v.hex()
                print(f"{k_str} {v_str}")
        finally:
            db.close()

    elif args.command == "check":
        try:
            db = KVDatabase(args.path)
            if getattr(args, "audit", False):
                _, audit_hash = db.check(return_hash=True)
                db.close()
                print(f"OK audit_hash={audit_hash}")
            else:
                db.check()
                db.close()
                print("OK")
        except Exception as e:
            print(f"CORRUPT: {e}")
            sys.exit(1)

    elif args.command == "compact":
        db = KVDatabase(args.path)
        try:
            db.compact(
                priority_retention=getattr(args, "priority_retention", False),
                target_max_bytes=getattr(args, "target_bytes", None)
            )
            print("OK")
        finally:
            db.close()

    elif args.command == "serve":
        max_bytes = args.max_bytes if args.max_bytes > 0 else None
        run_serve(args.path, max_bytes=max_bytes)

    elif args.command == "ui":
        from src.tui import run_tui
        run_tui(args.path)


if __name__ == "__main__":
    main()
