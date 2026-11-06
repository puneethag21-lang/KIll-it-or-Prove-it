#!/usr/bin/env python3
"""
Synterra KVDB - Top-level wrapper and entry point.
"""

import sys
from src.db import KVDatabase
from src.format import TYPE_PUT as PUT, TYPE_DELETE as DELETE, TYPE_COMMIT as COMMIT
from src.errors import DiskFullError, CorruptedDatabaseError
from src.cli import main
from src import telemetry

if __name__ == "__main__":
    main()
