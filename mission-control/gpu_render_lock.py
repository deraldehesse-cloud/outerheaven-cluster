#!/usr/bin/env python3
"""
gpu_render_lock.py - Centralized GPU Render Mutex & Preemption Controller
for Outer Heaven (Intel Arc A770 16GB VRAM).

Guarantees:
  1. Strict Sequential Execution: Never runs concurrent ComfyUI inferences or VLM passes.
  2. Two Priority Levels:
       - PRIORITY_HIGH (1): Megan Telegram Bot, Web-GUI Schnell-Render.
       - PRIORITY_LOW (2): Catlord Background Trainer.
  3. Low-Latency Preemption:
       - When a HIGH-priority request arrives, it immediately sets a preemption flag.
       - LOW-priority tasks check `is_preempt_requested()` before each step and yield immediately.
"""

import os
import sys
import time
import json
import fcntl
import logging
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Dict, Any

logger = logging.getLogger("gpu_render_lock")

LOCK_FILE_PATH = Path("/tmp/outerheaven_gpu_render.lock")
STATE_FILE_PATH = Path("/tmp/outerheaven_gpu_render.json")
PREEMPT_FILE_PATH = Path("/tmp/outerheaven_gpu_preempt.flag")

PRIORITY_HIGH = 1
PRIORITY_LOW = 2


def is_preempt_requested() -> bool:
    """Returns True if a High-Priority task is waiting for the GPU."""
    if PREEMPT_FILE_PATH.is_file():
        try:
            mtime = PREEMPT_FILE_PATH.stat().st_mtime
            # Discard flags older than 3 minutes as stale safety
            if time.time() - mtime > 180:
                PREEMPT_FILE_PATH.unlink(missing_ok=True)
                return False
            return True
        except Exception:
            return False
    return False


def set_preempt_flag(task_name: str = "high_priority_task"):
    """Signals all low-priority workers to yield the GPU immediately."""
    try:
        PREEMPT_FILE_PATH.write_text(json.dumps({
            "task_name": task_name,
            "pid": os.getpid(),
            "timestamp": time.time()
        }), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to set preempt flag: {e}")


def clear_preempt_flag():
    """Clears the preemption flag once the high priority task has finished or started."""
    try:
        PREEMPT_FILE_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def get_gpu_lock_status() -> Dict[str, Any]:
    """Returns current lock telemetry for Mission Control and logging."""
    status = {
        "locked": False,
        "preempt_requested": is_preempt_requested(),
        "current_task": None,
        "priority": None,
        "pid": None,
        "elapsed_seconds": 0.0,
        "last_high_priority_time": 0.0
    }
    if STATE_FILE_PATH.is_file():
        try:
            data = json.loads(STATE_FILE_PATH.read_text(encoding="utf-8"))
            status.update(data)
            started_at = data.get("started_at", 0)
            if data.get("locked") and started_at:
                status["elapsed_seconds"] = round(time.time() - started_at, 1)
        except Exception:
            pass
    return status


def record_state(locked: bool, priority: Optional[int] = None, task_name: Optional[str] = None):
    try:
        existing = {}
        if STATE_FILE_PATH.is_file():
            try:
                existing = json.loads(STATE_FILE_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass

        last_high = existing.get("last_high_priority_time", 0.0)
        if priority == PRIORITY_HIGH:
            last_high = time.time()

        payload = {
            "locked": locked,
            "priority": priority if locked else None,
            "current_task": task_name if locked else None,
            "pid": os.getpid() if locked else None,
            "started_at": time.time() if locked else None,
            "last_high_priority_time": last_high
        }
        STATE_FILE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to record lock state: {e}")


class GPURenderLock:
    """Inter-process mutual exclusion lock using fcntl.flock with priority preemption."""

    def __init__(self, priority: int = PRIORITY_HIGH, task_name: str = "unnamed_task", timeout: float = 120.0):
        self.priority = priority
        self.task_name = task_name
        self.timeout = timeout
        self.fd: Optional[int] = None

    def acquire(self) -> bool:
        LOCK_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

        if self.priority == PRIORITY_HIGH:
            set_preempt_flag(self.task_name)
        elif self.priority == PRIORITY_LOW:
            # Low-priority must NOT acquire if a high-priority request is active or pending
            if is_preempt_requested():
                logger.info(f"[{self.task_name}] Lock abgelehnt: High-Priority Preempt aktiv.")
                return False

            # Idle cooldown: low-priority tasks wait at least 15s after last high-priority task
            status = get_gpu_lock_status()
            last_high = status.get("last_high_priority_time", 0.0)
            if time.time() - last_high < 15.0:
                logger.info(f"[{self.task_name}] Lock abgelehnt: GPU im Cooldown nach High-Priority Task.")
                return False

        start_time = time.time()
        self.fd = os.open(str(LOCK_FILE_PATH), os.O_CREAT | os.O_RDWR)

        while True:
            try:
                if self.priority == PRIORITY_LOW:
                    # Low priority acquires non-blocking
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    record_state(True, self.priority, self.task_name)
                    logger.info(f"✔ [{self.task_name}] GPU-Lock (LOW) erworben.")
                    return True
                else:
                    # High priority attempts non-blocking, then waits briefly
                    try:
                        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        clear_preempt_flag()
                        record_state(True, self.priority, self.task_name)
                        logger.info(f"✔ [{self.task_name}] GPU-Lock (HIGH) erworben.")
                        return True
                    except (BlockingIOError, IOError):
                        pass

            except (BlockingIOError, IOError):
                if self.priority == PRIORITY_LOW:
                    os.close(self.fd)
                    self.fd = None
                    return False

            if time.time() - start_time > self.timeout:
                logger.error(f"[{self.task_name}] Timeout ({self.timeout}s) beim Warten auf GPU-Lock!")
                if self.fd is not None:
                    os.close(self.fd)
                    self.fd = None
                return False

            time.sleep(0.2)

    def release(self):
        if self.fd is not None:
            try:
                record_state(False)
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
            except Exception as e:
                logger.warning(f"Fehler beim Freigeben des GPU-Locks: {e}")
            finally:
                self.fd = None
                if self.priority == PRIORITY_HIGH:
                    clear_preempt_flag()
                logger.info(f"✔ [{self.task_name}] GPU-Lock freigegeben.")


@contextmanager
def gpu_render_scope(priority: int = PRIORITY_HIGH, task_name: str = "render_task", timeout: float = 120.0):
    """Context manager for acquiring and safely releasing the GPU lock."""
    lock = GPURenderLock(priority=priority, task_name=task_name, timeout=timeout)
    acquired = lock.acquire()
    if not acquired:
        raise TimeoutError(f"Konnte GPU-Lock für '{task_name}' (Prio {priority}) nicht erwerben.")
    try:
        yield lock
    finally:
        lock.release()
