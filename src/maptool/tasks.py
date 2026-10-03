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
# 切分用的上传缓存
# ============================================================
# 切分流程要上传两次会很难受（一次出预览、一次真正切），所以预览时把
# 文件内容按「字节指纹」缓存起来，前端只要回传指纹就能直接切。
# 只留最近几个，避免大投影把内存吃满。
SLICE_CACHE = OrderedDict()
SLICE_CACHE_LOCK = threading.Lock()
MAX_SLICE_CACHE = 6


def slice_key(content):
    """内容指纹：长度 + 首尾 64KB 的哈希。够区分不同文件，又不用扫全文。"""
    import hashlib
    h = hashlib.sha1()
    h.update(str(len(content)).encode("ascii"))
    h.update(content[:65536])
    h.update(content[-65536:])
    return h.hexdigest()[:16]


def cache_put_slice(content):
    key = slice_key(content)
    with SLICE_CACHE_LOCK:
        SLICE_CACHE[key] = content
        SLICE_CACHE.move_to_end(key)
        while len(SLICE_CACHE) > MAX_SLICE_CACHE:
            SLICE_CACHE.popitem(last=False)
    return key


def cache_get_slice(key):
    if not key:
        return None
    with SLICE_CACHE_LOCK:
        content = SLICE_CACHE.get(key)
        if content is not None:
            SLICE_CACHE.move_to_end(key)
        return content


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


