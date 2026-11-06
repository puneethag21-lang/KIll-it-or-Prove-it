"""
Crash-safe database compaction with Priority Retention.

Supports:
1. Standard Compaction: Eliminates tombstones and overwritten historical records,
   retaining 100% of live keys in the authoritative database.
2. Priority Retention Compaction: When storage limits are reached on flash memory,
   guarantees survival of high-priority audit tags (keys prefixed with 'crit:')
   while pruning older non-critical telemetry frames to reclaim space.
"""

import os
from typing import Optional, List, Tuple
from src.format import (
    TYPE_PUT, TYPE_COMMIT,
    encode_file_header, encode_record,
    RECORD_HEADER_SIZE, CRC_SIZE,
)
from src.storage import fsync_file, sync_directory_if_posix, file_lock


def compact_database(
    db,
    priority_retention: bool = False,
    target_max_bytes: Optional[int] = None,
) -> Tuple[int, int]:
    """
    Executes atomic, crash-safe compaction.
    Returns (retained_key_count, pruned_key_count).
    """
    db._ensure_open()

    with db.lock:
        with file_lock(db.lock_file):
            db._ensure_open()

            compact_temp = db.path + ".compact"
            if os.path.exists(compact_temp):
                try:
                    os.remove(compact_temp)
                except OSError:
                    pass

            all_keys = sorted(db.index.keys())

            if not priority_retention:
                retained_keys = all_keys
                pruned_count = 0
            else:
                crit_keys = [k for k in all_keys if k.startswith(b"crit:")]
                non_crit_keys = [k for k in all_keys if not k.startswith(b"crit:")]

                # Target capacity calculation
                target_budget = target_max_bytes
                if target_budget is None and db.max_bytes:
                    # Target 75% of max_bytes to leave write headroom on flash
                    target_budget = int(db.max_bytes * 0.75)

                if target_budget is None:
                    # If no target specified, prune oldest 50% of non-crit keys if any exist
                    keep_count = len(non_crit_keys) // 2
                    retained_non_crit = non_crit_keys[-keep_count:] if keep_count > 0 else []
                    retained_keys = sorted(crit_keys + retained_non_crit)
                    pruned_count = len(all_keys) - len(retained_keys)
                else:
                    # Calculate fixed overhead: 16B file header + 23B commit marker
                    overhead = 16 + 23
                    crit_bytes = sum(
                        RECORD_HEADER_SIZE + len(k) + db.index[k][1] + CRC_SIZE
                        for k in crit_keys
                    )
                    remaining_budget = max(0, target_budget - overhead - crit_bytes)

                    # Pack non-critical keys starting from newest (end of list)
                    retained_non_crit = []
                    acc_bytes = 0
                    for k in reversed(non_crit_keys):
                        entry_size = RECORD_HEADER_SIZE + len(k) + db.index[k][1] + CRC_SIZE
                        if acc_bytes + entry_size <= remaining_budget:
                            retained_non_crit.append(k)
                            acc_bytes += entry_size
                        else:
                            break

                    retained_keys = sorted(crit_keys + retained_non_crit)
                    pruned_count = len(all_keys) - len(retained_keys)

            txid = int.from_bytes(os.urandom(8), "little")

            # Write retained keys to temporary compaction file
            with open(compact_temp, "w+b") as temp_f:
                temp_f.write(encode_file_header())

                for key in retained_keys:
                    offset, length = db.index[key]
                    db.file.seek(offset)
                    val = db.file.read(length)
                    rec = encode_record(TYPE_PUT, txid, key, val)
                    temp_f.write(rec)

                commit_rec = encode_record(TYPE_COMMIT, txid)
                temp_f.write(commit_rec)

                fsync_file(temp_f)

            # Close current database handle before replacing (Windows lock requirement)
            db.file.close()
            db.file = None

            # Atomic file replacement
            os.replace(compact_temp, db.path)

            # Sync directory metadata on POSIX
            sync_directory_if_posix(db.path)

            # Reopen and rebuild in-memory index
            db.file = open(db.path, "r+b")
            db._recover()

            return len(retained_keys), pruned_count
