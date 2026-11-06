
import os
from kvdb import KVDatabase

filename = "compact_test.kv"

# Start fresh
for path in (filename, filename + ".lock",
             filename + ".compact.tmp"):
    if os.path.exists(path):
        os.remove(path)

db = KVDatabase(filename)

# Write multiple versions of the same key
db.put("name", "Puneetha")
db.put("name", "Rahul")
db.put("name", "Alex")

# Add another live key
db.put("college", "Srinivas")

# Delete another key
db.put("old_data", "This will be deleted")
db.delete("old_data")

before = os.path.getsize(filename)

print("Size before:", before)
print("Name before:", db.get("name"))

# Compact
db.compact()

after = os.path.getsize(filename)

print("Size after:", after)
print("Name after:", db.get("name"))
print("College after:", db.get("college"))
print("Old data:", db.get("old_data"))

db.close()
