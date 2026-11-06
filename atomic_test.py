
import os
from kvdb import KVDatabase, PUT

filename = "atomic_test.kv"

# Start with a fresh database
for path in (filename, filename + ".lock"):
    if os.path.exists(path):
        os.remove(path)

db = KVDatabase(filename)

# Create an uncommitted transaction
txid = 12345

record = db._record(
    PUT, b"name", b"Puneetha", txid
)

# Write the record but deliberately omit COMMIT
with db.file_lock if False else open(filename, "ab") as f:
    f.write(record)
    f.flush()
    os.fsync(f.fileno())

db.close()

# Reopen and recover
db = KVDatabase(filename)

print("Name:", db.get("name"))
print("Total keys:", db.check())

db.close()
