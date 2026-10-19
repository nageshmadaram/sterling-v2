"""Report stalled event-loop stacks without locals, source text or secrets."""
from contextlib import asynccontextmanager
import asyncio
import logging
from pathlib import Path
import sys
import threading
import time
import traceback

log=logging.getLogger(__name__)

def stack_locations(thread_id):
    frame=sys._current_frames().get(thread_id)
    if frame is None:
        return "unavailable"
    return " > ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}"
                      for f in traceback.extract_stack(frame)[-30:])

@asynccontextmanager
async def watch_event_loop(*, threshold=30.0, check_interval=1.0):
    stop=threading.Event()
    thread_id=threading.get_ident()
    last_beat=[time.monotonic()]
    def monitor():
        last_report=0.0
        while not stop.wait(check_interval):
            now=time.monotonic()
            lag=now-last_beat[0]
            if lag>=threshold and now-last_report>=threshold:
                last_report=now
                log.warning("Event loop stalled %.1fs; locations=%s",lag,stack_locations(thread_id))
    async def pulse():
        while True:
            last_beat[0]=time.monotonic()
            await asyncio.sleep(min(1.0,threshold/4))
    task=asyncio.create_task(pulse(),name="event-loop-watchdog-heartbeat")
    thread=threading.Thread(target=monitor,name="sterling-loop-watchdog",daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        # No diagnostic may outlive the lifespan it observes.
        await asyncio.to_thread(thread.join)
