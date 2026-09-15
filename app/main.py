"""FastAPI application: lifespan, routes, the /ws receive and broadcast loops, and the uvicorn entry point.

Run from the project root:  python -m app.main   (or: uvicorn app.main:app --reload)
"""

import asyncio
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app.db import seed_DB
from app.i18n import DEFAULT_LANGUAGE, MESSAGES, Language
from app.models import Comment, CommentCreateMessage, Flag, FlagCreateMessage, incoming_adapter, render


def next_slot(app: FastAPI) -> str:
    slot = app.state.current_slot
    app.state.current_slot = "bottom" if slot == "top" else "top"
    return slot


async def sender_loop(websocket: WebSocket) -> None:
    """Runs for the lifetime of one connection. Send a freshly submitted statement as soon
    as it arrives on the queue, otherwise a random one from the DB every 3-7 s."""
    app = websocket.app
    queue = app.state.new_comments
    while True:
        try:
            statement = await asyncio.wait_for(queue.get(), timeout=random.randint(3, 7))
            new = True
        except asyncio.TimeoutError:
            statement = random.choice(list(app.state.DB.values()))
            new = False
        msg = render(statement, next_slot(app), new=new)
        await websocket.send_json(msg.model_dump(mode="json"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.DB = seed_DB()
    app.state.current_slot = "top"
    app.state.new_comments = asyncio.Queue()
    for c in app.state.DB.values():
        app.state.new_comments.put_nowait(c)
    yield


app = FastAPI(title="Comment2Conversation", version="0.1", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/db")
def get_DB():
    return [c.model_dump(mode="json") for c in app.state.DB.values()]


def _pick_language(node, lang: str):
    """Recursively replace every {lang: text} leaf with the text for `lang`
    (falling back to the default language)."""
    if isinstance(node, dict):
        if lang in node or DEFAULT_LANGUAGE in node:
            return node.get(lang) if node.get(lang) is not None else node.get(DEFAULT_LANGUAGE)
        return {k: _pick_language(v, lang) for k, v in node.items()}
    return node


@app.get("/messages")
def get_messages(lang: Language | None = None):
    """All static user-facing strings from i18n/messages.yaml.

    Without `lang`, every leaf is a {language_code: text} mapping.
    With `?lang=nl`, the leaves are collapsed to that language's text.
    """
    if lang is None:
        return MESSAGES
    return _pick_language(MESSAGES, lang)


async def receiver_loop(websocket: WebSocket) -> None:
    """Runs for the lifetime of one connection, reacting to incoming messages."""
    state = websocket.app.state
    while True:
        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect:
            return  # normal exit: client hung up

        try:
            message = incoming_adapter.validate_python(data)
        except ValidationError as e:
            await websocket.send_json({"error": e.errors()})
            continue

        match message:
            case CommentCreateMessage(payload=comment_create):
                topics = comment_create.topics or state.DB[comment_create.reply_to].topics
                comment = Comment(**comment_create.model_dump(exclude={"topics"}),
                                  topics=topics)
                state.DB[comment.ID] = comment
                state.new_comments.put_nowait(comment)
            case FlagCreateMessage(payload=flag_create):
                flag = Flag(**flag_create.model_dump())
                state.DB[flag.comment_ID].flag = flag


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    # data = await websocket.receive_json()  # this would consume the LanguageChoice
    sender = asyncio.create_task(sender_loop(websocket))
    try:
        await receiver_loop(websocket)
    finally:
        sender.cancel()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
