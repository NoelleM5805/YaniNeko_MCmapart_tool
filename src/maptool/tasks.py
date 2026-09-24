# -*- coding: utf-8 -*-
from collections import OrderedDict
import threading
import time
import uuid

from .config import KEEP_TASKS, TASKS, TASK_LOCK


# ============================================================
# 图片缓存
# ============================================================
IMAGE_CACHE = OrderedDict()
IMAGE_CACHE_LOCK = threading.Lock()
MAX_IMAGE_CACHE = 12


def cache_put_image(img):
    sid = uuid.uuid4().hex[:12]
    with IMAGE_CACHE_LOCK:
        IMAGE_CACHE[sid] = img
        IMAGE_CACHE.move_to_end(sid)
        while len(IMAGE_CACHE) > MAX_IMAGE_CACHE:
            IMAGE_CACHE.popitem(last=False)
    return sid


def cache_get_image(sid):
    if not sid:
        return None
    with IMAGE_CACHE_LOCK:
        img = IMAGE_CACHE.get(sid)
        if img is not None:
            IMAGE_CACHE.move_to_end(sid)
        return img



# ============================================================
# 任务系统
# ============================================================
def add_log(task, msg):
    with TASK_LOCK:
        task["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(task["logs"]) > 800:
            del task["logs"][:len(task["logs"]) - 800]


def create_task(name):
    tid = uuid.uuid4().hex[:12]
    TASKS[tid] = {
        "id": tid, "name": name,
        "running": True, "done": False,
        "logs": [], "error": None,
        "result_bytes": None, "result_name": None,
        "result": None, "started": time.time(),
    }
    if len(TASKS) > KEEP_TASKS:
        old = sorted(TASKS.keys(), key=lambda k: TASKS[k].get("started", 0))
        for k in old[:-KEEP_TASKS]:
            TASKS.pop(k, None)
    return tid


def finish_task(tid):
    t = TASKS.get(tid)
    if t:
        t["running"] = False
        t["done"] = True


