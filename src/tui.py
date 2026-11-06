"""
Synterra KVDB - Minimalist Interactive Terminal UI.

OpenCode-inspired aesthetic:
- Deep midnight navy palette
- macOS-style window controls
- Pixel-style SYNTERRA KVDB typography
- Real-time database metrics (active keys, on-disk size, durable status)
- Direct integration with core crash-safe KVDatabase engine
"""

import os
import sys
import json
import time
import threading
from typing import Optional, List, Tuple
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from src.db import KVDatabase
from src.errors import KVDBError, DiskFullError, CorruptedDatabaseError

# Enable Virtual Terminal Processing on Windows
if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        os.system("")


# 24-bit TrueColor Palette (Catppuccin Mocha / OpenCode Midnight)
ESC = "\033["
RESET = f"{ESC}0m"
BOLD = f"{ESC}1m"
DIM = f"{ESC}2m"

C_RED = f"{ESC}38;2;255;95;86m"
C_YELLOW = f"{ESC}38;2;255;189;46m"
C_GREEN = f"{ESC}38;2;39;201;63m"

C_LOGO = f"{ESC}38;2;180;190;254m"       # Soft periwinkle/lavender
C_VERSION = f"{ESC}38;2;108;112;134m"    # Dim lavender-gray
C_CMD = f"{ESC}38;2;205;214;244m\033[1m" # Bold bright lavender
C_DESC = f"{ESC}38;2;127;132;156m"       # Muted slate
C_PROMPT = f"{ESC}38;2;203;166;247m\033[1m" # Vibrant purple
C_BOX_BG = f"{ESC}48;2;30;30;46m"        # Dark purple-navy container background
C_BOX_BORDER = f"{ESC}38;2;69;71;90m"    # Subtle border
C_FOOTER = f"{ESC}38;2;88;91;112m"       # Subdued footer
C_SUCCESS = f"{ESC}38;2;166;227;161m\033[1m" # Soft emerald
C_WARN = f"{ESC}38;2;249;226;175m"       # Amber
C_ERROR = f"{ESC}38;2;243;139;168m"      # Soft coral
C_VAL = f"{ESC}38;2;137;180;250m"        # Soft cyan-blue


PIXEL_LOGO = [
    "████  █   █ █   █ █████ █████ ████  ████   ███        █   █ █   █ ████  ████ ",
    "█     █   █ ██  █   █   █     █   █ █   █ █   █       █  █  █   █ █   █ █   █",
    "████  █████ █ █ █   █   ████  ████  ████  █████       ███   █   █ █   █ ████ ",
    "   █      █ █  ██   █   █     █ █   █ █   █   █       █  █   █ █  █   █ █   █",
    "████      █ █   █   █   █████ █  ██ █  ██ █   █       █   █   █   ████  ████ ",
]

COMMAND_MENU = [
    ("/help", "show help"),
    ("/put", "set a key-value"),
    ("/get", "read a key"),
    ("/delete", "remove a key"),
    ("/batch", "apply batch (JSON)"),
    ("/dump", "list all keys"),
    ("/check", "verify integrity"),
    ("/compact", "shrink the database"),
    ("/serve", "start HTTP server"),
    ("/exit", "exit the app"),
]


def format_bytes(n: int) -> str:
    """Format byte size into human readable string."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


class EmbeddedKVServer(BaseHTTPRequestHandler):
    """Minimal HTTP REST server for /serve command."""
    db: Optional[KVDatabase] = None

    def log_message(self, format, *args):
        # Suppress standard logging to keep terminal UI pristine
        pass

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            size = os.path.getsize(self.db.path) if os.path.exists(self.db.path) else 0
            self._send_json(200, {
                "database": os.path.basename(self.db.path),
                "active_keys": len(self.db.index),
                "file_size": size,
                "status": "DURABLE (fsync)",
                "version": "1.0.0"
            })
        elif parsed.path == "/get":
            qs = parse_qs(parsed.query)
            key = qs.get("key", [""])[0]
            val = self.db.get(key)
            if val is None:
                self._send_json(404, {"error": "NOTFOUND", "key": key})
            else:
                try:
                    v_str = val.decode("utf-8")
                except UnicodeDecodeError:
                    v_str = val.hex()
                self._send_json(200, {"key": key, "value": v_str})
        elif parsed.path == "/dump":
            items = []
            for k, v in self.db.dump():
                try:
                    k_str = k.decode("utf-8")
                except UnicodeDecodeError:
                    k_str = k.hex()
                try:
                    v_str = v.decode("utf-8")
                except UnicodeDecodeError:
                    v_str = v.hex()
                items.append({"key": k_str, "value": v_str})
            self._send_json(200, {"keys": items, "count": len(items)})
        elif parsed.path == "/check":
            _, audit_hash = self.db.check(return_hash=True)
            self._send_json(200, {"status": "OK", "audit_hash": audit_hash, "keys": len(self.db.index)})
        else:
            self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/put":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
                k = payload.get("key")
                v = payload.get("value")
                if not k or v is None:
                    self._send_json(400, {"error": "Missing key or value"})
                    return
                self.db.put(k, str(v))
                self._send_json(200, {"status": "ACK", "key": k})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
        else:
            self._send_json(404, {"error": "Not Found"})


class SynterraTUI:
    """Main interactive terminal controller."""

    def __init__(self, db_path: str = "synterra.kv"):
        self.db_path = os.path.abspath(db_path)
        self.db = KVDatabase(self.db_path)
        self.version = "v1.0.0"
        self.last_output: Optional[Tuple[str, str]] = None  # (status_type, message)
        self.http_server: Optional[HTTPServer] = None
        self.http_thread: Optional[threading.Thread] = None
        self.http_port: Optional[int] = None

    def render_screen(self):
        """Renders the complete OpenCode terminal layout."""
        # Clear screen and return cursor to home
        sys.stdout.write(f"{ESC}2J{ESC}H")

        # 1. macOS-style title bar
        dots = f"{C_RED}● {C_YELLOW}● {C_GREEN}●{RESET}"
        title = f"{C_VERSION}synterra{RESET}"
        sys.stdout.write(f"\n   {dots}                                {title}\n\n")

        # 2. Pixel-art Banner
        for line in PIXEL_LOGO:
            sys.stdout.write(f"   {C_LOGO}{line}{RESET}\n")
        sys.stdout.write(f"                                                                       {C_VERSION}{self.version}{RESET}\n\n")

        # 3. Centered Command Menu
        for cmd, desc in COMMAND_MENU:
            sys.stdout.write(f"               {C_CMD}{cmd:<12}{RESET}{C_DESC}{desc}{RESET}\n")
        sys.stdout.write("\n")

        # 4. Command Output Feedback Card (if any command was executed)
        if self.last_output:
            status_type, msg = self.last_output
            color = C_SUCCESS if status_type == "success" else (C_WARN if status_type == "warn" else C_ERROR)
            lines = msg.splitlines()
            for l in lines[:10]:
                sys.stdout.write(f"     {color}›{RESET} {l}\n")
            if len(lines) > 10:
                sys.stdout.write(f"     {C_DESC}... ({len(lines) - 10} more lines){RESET}\n")
            sys.stdout.write("\n")
        else:
            sys.stdout.write("\n\n")

        # 5. Database Status Metrics
        db_file = os.path.basename(self.db_path)
        key_count = len(self.db.index)
        file_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        size_str = format_bytes(file_size)

        # 6. Minimal Footer & Input Box
        box_width = 72
        pad_box = " " * (box_width - 6)
        sys.stdout.write(f"   {C_BOX_BG}  {C_PROMPT}>{RESET}{C_BOX_BG} █{pad_box}{RESET}\n")
        footer_right = f"{C_VERSION}{db_file}  •  {key_count} keys  •  {size_str}  •  {C_SUCCESS}DURABLE{RESET}"
        sys.stdout.write(f"   {C_DESC}enter command{RESET}                       {footer_right}\n\n")
        sys.stdout.flush()

    def execute_command(self, raw_input: str) -> bool:
        """
        Parses and executes user commands against the real database.
        Returns False to exit the loop, True to continue.
        """
        line = raw_input.strip()
        if not line:
            return True

        # Strip leading slash if present
        cmd_text = line[1:] if line.startswith("/") else line
        parts = cmd_text.split(maxsplit=2)
        cmd = parts[0].lower() if parts else ""
        args = parts[1:]

        try:
            if cmd in ("exit", "quit", "q"):
                self.db.close()
                if self.http_server:
                    self.http_server.shutdown()
                print(f"\n   {C_SUCCESS}Database closed cleanly. Goodbye!{RESET}\n")
                return False

            elif cmd in ("help", "h"):
                help_msg = (
                    "Available Commands:\n"
                    "  /put <key> <value>      Set key-value pair\n"
                    "  /get <key>              Retrieve value for key\n"
                    "  /delete <key>           Remove key from database\n"
                    "  /batch <json|ops|file>  Execute atomic batch\n"
                    "  /dump                   Display all keys and values\n"
                    "  /check                  Verify database CRC & Merkle audit hash\n"
                    "  /compact                Execute crash-safe compaction\n"
                    "  /serve [port]           Start embedded REST HTTP server\n"
                    "  /clear                  Redraw clean terminal screen\n"
                    "  /exit                   Exit Synterra KVDB"
                )
                self.last_output = ("success", help_msg)

            elif cmd == "put":
                if len(args) < 2:
                    self.last_output = ("warn", "Usage: /put <key> <value>")
                    return True
                k = args[0]
                v = args[1]
                self.db.put(k, v)
                self.last_output = ("success", "ACK")

            elif cmd == "get":
                if len(args) < 1:
                    self.last_output = ("warn", "Usage: /get <key>")
                    return True
                val = self.db.get(args[0])
                if val is None:
                    self.last_output = ("warn", "NOTFOUND")
                else:
                    try:
                        v_str = val.decode("utf-8")
                    except UnicodeDecodeError:
                        v_str = val.hex()
                    self.last_output = ("success", v_str)

            elif cmd in ("del", "delete", "remove"):
                if len(args) < 1:
                    self.last_output = ("warn", "Usage: /delete <key>")
                    return True
                self.db.delete(args[0])
                self.last_output = ("success", "ACK")

            elif cmd == "batch":
                if len(args) < 1:
                    self.last_output = ("warn", "Usage: /batch <json_string | filename | put k1 v1; put k2 v2>")
                    return True

                batch_arg = " ".join(args)
                ops = []

                # Format 1: JSON array: [{"op":"put","key":"...","val":"..."}, ...] or {"k1":"v1"}
                if batch_arg.startswith("[") or batch_arg.startswith("{"):
                    try:
                        parsed_json = json.loads(batch_arg)
                        if isinstance(parsed_json, dict):
                            for k, v in parsed_json.items():
                                ops.append(("put", str(k), str(v)))
                        elif isinstance(parsed_json, list):
                            for item in parsed_json:
                                op_type = item.get("op", "put").lower()
                                k = str(item.get("key", ""))
                                v = str(item.get("val", item.get("value", "")))
                                ops.append((op_type, k, v))
                    except Exception as e:
                        self.last_output = ("error", f"Invalid JSON batch: {e}")
                        return True

                # Format 2: File on disk
                elif os.path.exists(batch_arg):
                    with open(batch_arg, "r", encoding="utf-8") as f:
                        for fline in f:
                            fline = fline.strip()
                            if not fline or fline.startswith("#"):
                                continue
                            sp = fline.split(maxsplit=2)
                            action = sp[0].lower()
                            if action == "put" and len(sp) == 3:
                                ops.append(("put", sp[1], sp[2]))
                            elif action in ("del", "delete") and len(sp) >= 2:
                                ops.append(("del", sp[1], ""))

                # Format 3: Semicolon separated string
                else:
                    for sub in batch_arg.split(";"):
                        sub = sub.strip()
                        if not sub:
                            continue
                        sp = sub.split(maxsplit=2)
                        action = sp[0].lower()
                        if action == "put" and len(sp) == 3:
                            ops.append(("put", sp[1], sp[2]))
                        elif action in ("del", "delete") and len(sp) >= 2:
                            ops.append(("del", sp[1], ""))

                if not ops:
                    self.last_output = ("warn", "No valid operations found in batch")
                    return True

                self.db.batch(ops)
                self.last_output = ("success", f"ACK: Applied {len(ops)} operations atomically")

            elif cmd == "dump":
                items = self.db.dump()
                if not items:
                    self.last_output = ("warn", "Database is empty (0 keys)")
                else:
                    lines = [f"{k.decode(errors='replace')} {v.decode(errors='replace')}" for k, v in items]
                    self.last_output = ("success", f"Total: {len(items)} keys\n" + "\n".join(lines))

            elif cmd == "check":
                active_count, audit_hash = self.db.check(return_hash=True)
                msg = (
                    f"OK: Database integrity verified\n"
                    f"Active Keys: {active_count}\n"
                    f"Audit Hash:  {audit_hash}"
                )
                self.last_output = ("success", msg)

            elif cmd == "compact":
                size_before = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
                self.db.compact()
                size_after = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
                reclaimed = max(0, size_before - size_after)
                msg = (
                    f"OK: Compaction complete\n"
                    f"Before: {format_bytes(size_before)}  →  After: {format_bytes(size_after)} "
                    f"(Reclaimed: {format_bytes(reclaimed)})"
                )
                self.last_output = ("success", msg)

            elif cmd == "serve":
                port = int(args[0]) if args and args[0].isdigit() else 8080
                if self.http_server:
                    self.last_output = ("warn", f"HTTP server already running on port {self.http_port}")
                    return True

                EmbeddedKVServer.db = self.db
                self.http_port = port
                try:
                    self.http_server = HTTPServer(("127.0.0.1", port), EmbeddedKVServer)
                    self.http_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
                    self.http_thread.start()
                    msg = (
                        f"OK: HTTP Server listening on http://127.0.0.1:{port}\n"
                        f"Endpoints:\n"
                        f"  GET  http://127.0.0.1:{port}/         (Database status)\n"
                        f"  GET  http://127.0.0.1:{port}/get?key=X (Read key)\n"
                        f"  POST http://127.0.0.1:{port}/put      (Body: {{\"key\":\"...\",\"value\":\"...\"}})\n"
                        f"  GET  http://127.0.0.1:{port}/dump      (List all keys)\n"
                        f"  GET  http://127.0.0.1:{port}/check     (Integrity check)"
                    )
                    self.last_output = ("success", msg)
                except Exception as e:
                    self.last_output = ("error", f"Failed to start server on port {port}: {e}")

            elif cmd == "clear":
                self.last_output = None

            else:
                self.last_output = ("error", f"Unknown command: '{line}'. Type /help for command list.")

        except DiskFullError as e:
            self.last_output = ("error", f"DiskFullError: {e}")
        except CorruptedDatabaseError as e:
            self.last_output = ("error", f"CorruptedDatabaseError: {e}")
        except Exception as e:
            self.last_output = ("error", f"Error: {e}")

        return True

    def run(self):
        """Main interaction loop."""
        while True:
            self.render_screen()
            try:
                # Prompt indicator
                user_cmd = input(f"   {C_PROMPT}>{RESET} ")
                should_continue = self.execute_command(user_cmd)
                if not should_continue:
                    break
            except (KeyboardInterrupt, EOFError):
                print(f"\n\n   {C_SUCCESS}Exiting Synterra KVDB. All promises durable.{RESET}\n")
                self.db.close()
                if self.http_server:
                    self.http_server.shutdown()
                break


def run_tui(db_path: str = "synterra.kv"):
    """Convenience launcher for the terminal UI."""
    tui = SynterraTUI(db_path)
    tui.run()


if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 else "synterra.kv"
    run_tui(db_file)
