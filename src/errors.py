"""
Custom exception hierarchy for the KVDB embedded database.
"""

class KVDBError(Exception):
    """Base exception for all KVDB errors."""
    pass


class DiskFullError(KVDBError):
    """Raised when an operation would exceed max_bytes limit."""
    pass


class CorruptedDatabaseError(KVDBError):
    """Raised when an authoritative file header is fundamentally invalid."""
    pass


class DatabaseClosedError(KVDBError):
    """Raised when attempting an operation on a closed database."""
    pass
