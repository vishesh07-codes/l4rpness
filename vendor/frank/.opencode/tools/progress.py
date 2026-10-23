import time
import queue

_queue = queue.Queue()

def push(tool, status, message=""):
    _queue.put_nowait({"tool": tool, "status": status, "message": message, "time": time.time()})
