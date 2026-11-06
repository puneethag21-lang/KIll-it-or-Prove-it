
from kvdb import KVDatabase, PUT, COMMIT

filename = "commit_test.kv"
txid = 54321

# Create database
db = KVDatabase(filename)

# Prepare two records
record1 = db._record(
    PUT, b"name", b"Puneetha", txid
)

record2 = db._record(
    PUT, b"college", b"Srinivas", txid
)

# Create transaction commit marker
commit = db._record(COMMIT, txid=txid)

# Write records and commit
with db.file_lock if False else open(filename, "ab") as f:
    f.write(record1 + record2 + commit)
    f.flush()
    import os
    os.fsync(f.fileno())

db.close()

# Reopen database
db = KVDatabase(filename)

print("Name:", db.get("name"))
print("College:", db.get("college"))
print("Total keys:", db.check())

db.close()
