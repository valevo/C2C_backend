"""FastAPI application: lifespan, routes, and the uvicorn entry point.

Run from the project root:  python -m app.main   (or: uvicorn app.main:app --reload)
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import seed_DB
from app.ws.handlers import router as ws_router
from app.ws.sender import sender_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.DB = seed_DB()
    app.state.current_slot = "top"
    app.state.new_comments = asyncio.Queue()
    for c in app.state.DB.values():
        app.state.new_comments.put_nowait(c)
    sender = asyncio.create_task(sender_loop(app))
    sender.add_done_callback(lambda t: t.cancelled() or t.exception() and t.print_stack())
    yield
    sender.cancel()


app = FastAPI(title="Comment2Conversation", version="0.1", lifespan=lifespan)
app.include_router(ws_router)


@app.get("/db")
def get_DB():
    return [c.model_dump(mode="json") for c in app.state.DB.values()]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
