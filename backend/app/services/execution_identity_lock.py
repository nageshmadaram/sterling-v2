"""Serialize account transitions with canonical submissions in this process.

The journal additionally fences in-flight submissions durably across processes.
Locks are loop-local because tests and service restarts create new event loops.
"""
import asyncio
from weakref import WeakKeyDictionary

_locks: WeakKeyDictionary = WeakKeyDictionary()


def account_lock(uid: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _locks.setdefault(loop, {})
    return locks.setdefault(str(uid), asyncio.Lock())
