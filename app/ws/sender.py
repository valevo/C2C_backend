"""The broadcast loop: one per application, pushes a statement to every client on a schedule."""

import asyncio
import random

from fastapi import FastAPI

from app.models import render
from app.ws.manager import mgr


def next_slot(app: FastAPI) -> str:
    slot = app.state.current_slot
    app.state.current_slot = "bottom" if slot == "top" else "top"
    return slot


async def sender_loop(app: FastAPI) -> None:
    """Send a freshly submitted statement as soon as it arrives on the queue,
    otherwise a random one from the DB every 3-7 s."""
    queue = app.state.new_comments
    while True:
        try:
            statement = await asyncio.wait_for(queue.get(), timeout=random.randint(3, 7))
            new = True
        except asyncio.TimeoutError:
            statement = random.choice(list(app.state.DB.values()))
            new = False
        msg = render(statement, next_slot(app), new=new)
        await mgr.broadcast(msg.model_dump(mode="json"))
