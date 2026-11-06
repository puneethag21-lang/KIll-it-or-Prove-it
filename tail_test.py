
from kvdb import KVDatabase

# Simulate an interrupted write by adding an incomplete header
with open("database.kv", "ab") as f:
    f.write(b"KV")

print("Incomplete record added.")

# Reopen database: recovery should remove incomplete tail
db = KVDatabase("database.kv")

print("Database recovered!")
print("name:", db.get("name"))
print("crash_test:", db.get("crash_test"))

db.close()
