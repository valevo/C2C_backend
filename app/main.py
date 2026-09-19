"""FastAPI application: lifespan, routes, the /ws receive and broadcast loops, and the uvicorn entry point.

Run from the project root:  python -m app.main   (or: uvicorn app.main:app --reload)
"""

import asyncio
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from starlette.datastructures import State

from app.conversation import SampledConversation
from app.data import Comments, ConversationStarters, make_random_comments
from app.i18n import DEFAULT_LANGUAGE, MESSAGES, Language
from app.models import CommentCreateMessage, Flag, FlagCreateMessage, incoming_adapter, render

INTERVAL=2
MIN_CONVO_LEN=3
N_RANDOM_COMMENTS=30


def next_slot(app_state: State) -> str:
    slot = app_state.current_slot
    app_state.current_slot = "bottom" if slot == "top" else "top"
    return slot




@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.starters = ConversationStarters.from_csv()
    app.state.comments = Comments(
        make_random_comments(app.state.starters, n=N_RANDOM_COMMENTS),
        starters=app.state.starters,
    )
    app.state.current_slot = "top"
    app.state.Q = asyncio.Queue()
    app.state.connections: set[WebSocket] = set()

    # One conversation for everyone: a single sender loop runs for the lifetime of
    # the app and broadcasts each step to every open connection.
    sender = asyncio.create_task(sender_loop(app.state), name="sender_loop")
    yield
    sender.cancel()
    await asyncio.gather(sender, return_exceptions=True)


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
    """All statements: the static conversation starters followed by the comments."""
    return [s.model_dump(mode="json") for s in (*app.state.starters, *app.state.comments)]


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


async def receiver_loop(websocket):
    state = websocket.app.state
    while True:
        # check if something can be received
        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect:
            return  # normal exit: client hung up

        # check if the received data can be parsed into any of the models
        try:
            message = incoming_adapter.validate_python(data)
        except ValidationError as e:
            await websocket.send_json({"error": e.errors()})
            continue

        match message:
            case CommentCreateMessage(payload=comment_create):
                # Comments.add builds the Comment and fills in topics from the parent
                # (a starter or another comment) when the client sent none.
                try:
                    comment = state.comments.add(comment_create)
                except ValueError as e:
                    await websocket.send_json({"error": str(e)})
                    continue
                state.Q.put_nowait(comment)
            case FlagCreateMessage(payload=flag_create):
                flag = Flag(**flag_create.model_dump())
                target = state.comments.get(flag.comment_ID)
                if target is None:
                    await websocket.send_json({"error": f"unknown comment ID {flag.comment_ID}"})
                    continue
                target.flag = flag




async def broadcast(app_state: State, data: dict) -> None:
    """Send `data` to every open connection; drop connections that fail."""
    connections = list(app_state.connections)
    results = await asyncio.gather(
        *(ws.send_json(data) for ws in connections), return_exceptions=True
    )
    for ws, result in zip(connections, results):
        if isinstance(result, Exception):
            app_state.connections.discard(ws)


async def sender_loop(app_state: State):
    """Drive the shared conversation and broadcast each step to all connections.

    Runs once per app (started in `lifespan`), not once per connection.
    """
    queue = app_state.Q
    
    loop = asyncio.get_running_loop()
    next_at = loop.time()
    without_interception = MIN_CONVO_LEN
    new_convo = lambda start=None: SampledConversation(app_state.starters, app_state.comments, start)
    convo = new_convo()
    while True:
        next_at += INTERVAL
        await asyncio.sleep(max(0, next_at - loop.time()))


        
        is_new = without_interception < 1 and not queue.empty()
        if is_new:
            cur = queue.get_nowait()
            
            without_interception = MIN_CONVO_LEN
            # `cur` is already stored: the receiver added it via app_state.comments.add
            
            # here, this comment starts its own conversation together with its parent
            # (if there is one)
            convo = new_convo(cur)  # TODO: include parent
        else:
            try:
                cur = next(convo)
            except StopIteration:
                convo = new_convo()
                cur = next(convo)
            without_interception -= 1

        msg = render(cur, next_slot(app_state), new=is_new)
        await broadcast(app_state, msg.model_dump(mode="json"))



@app.websocket("/ws")
async def main(websocket: WebSocket):
    await websocket.accept()
    connections = websocket.app.state.connections
    connections.add(websocket)
    try:
        # the shared sender_loop pushes the conversation to this socket;
        # here we only listen for comments and flags from this client
        await receiver_loop(websocket)
    finally:
        connections.discard(websocket)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
