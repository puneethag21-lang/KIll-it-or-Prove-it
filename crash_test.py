
import os
from kvdb import KVDatabase

db = KVDatabase("database.kv")
db.put("crash_test", "SURVIVED")
print("ACK: Data saved", flush=True)

# Simulate sudden process termination
os._exit(0)
