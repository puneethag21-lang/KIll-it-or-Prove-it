
import multiprocessing
from kvdb import KVDatabase


def writer(process_id):
    db = KVDatabase("concurrency.kv")

    for i in range(100):
        key = f"process_{process_id}_key_{i}"
        value = f"value_{i}"

        db.put(key, value)

    db.close()
    print(f"Process {process_id} completed")


if __name__ == "__main__":
    p1 = multiprocessing.Process(target=writer, args=(1,))
    p2 = multiprocessing.Process(target=writer, args=(2,))

    p1.start()
    p2.start()

    p1.join()
    p2.join()

    db = KVDatabase("concurrency.kv")

    print("Total keys:", db.check())

    db.close()

