"""Await blocking work without abandoning an in-flight durable transaction."""
import asyncio

async def run_blocking(fn, *args, **kwargs):
    work = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        await asyncio.gather(work, return_exceptions=True)
        raise
