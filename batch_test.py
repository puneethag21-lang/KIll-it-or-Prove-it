
from kvdb import KVDatabase

db = KVDatabase("batch_test.kv")

operations = [
    ("put", "name", "Puneetha"),
    ("put", "college", "Srinivas"),
    ("put", "project", "Synterra")
]

db.batch(operations)

print("Batch completed!")

print("Name:", db.get("name"))
print("College:", db.get("college"))
print("Project:", db.get("project"))

db.close()
