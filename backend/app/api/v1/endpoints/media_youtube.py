"""Public, read-only YouTube library for Sterling TV (see app/services/youtube_library.py).

The TV page runs in a sandboxed frame without a sign-in, so this route needs no token and allows any
origin. It only returns public data for a fixed channel list and takes no URL or query input.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services import youtube_library

router = APIRouter(prefix="/media/youtube", tags=["media"])


@router.get("/library")
async def youtube_library_route() -> JSONResponse:
    data = await youtube_library.library()
    return JSONResponse(data, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"})
