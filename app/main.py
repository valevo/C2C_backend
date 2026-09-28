"""FastAPI application: lifespan, routes, the /ws receive and broadcast loops, and the uvicorn entry point.

Run from the project root:  python -m app.main   (or: uvicorn app.main:app --reload)
"""

import asyncio
import random
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import State

from app.conversation import SampledConversation
from app.data import Comments, ConversationStarters, make_random_comments
from app.i18n import DEFAULT_LANGUAGE, MESSAGES, Language
from app.models import CommentCreateMessage, ConversationStarter, Flag, FlagCreateMessage, incoming_adapter, render

INTERVAL=20
LONG_INTERVAL=40  # after a conversation starter or a new comment: INTERVAL + the frontend's 20s animation
MIN_CONVO_LEN=3
N_RANDOM_COMMENTS=30
IMAGES_DIR = Path(__file__).parent / "images"


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
    app.state.last_msg: dict | None = None  # sent to clients as soon as they connect

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


class Image(BaseModel):
    name: str = Field(description="File name, e.g. c2cillustrations1.svg")
    url: str = Field(description="Path the file is served under, e.g. /images/c2cillustrations1.svg")
    svg: str | None = Field(None, description="The SVG markup; only present with ?inline=true")


@app.get("/images", response_model=list[Image], response_model_exclude_none=True)
def list_images(inline: bool = False):
    """The SVG images in app/images, each with the URL it is served under.

    With `?inline=true`, each entry also carries the SVG markup itself,
    so the frontend can render all images without further requests.
    """
    return [
        Image(
            name=path.name,
            url=f"/images/{path.name}",
            svg=path.read_text(encoding="utf-8") if inline else None,
        )
        for path in sorted(IMAGES_DIR.glob("*.svg"))
    ]


# static images for the frontend, e.g. GET /images/c2cillustrations1.svg
# (mounted after the /images route so that route is matched first)
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")


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
                    # the comment should actually not become part of the pool 
                    # samplable comments yet 
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
    # sampled Comments sent since the current conversation's start (the starter / new
    # comment and its repeat don't count); a queued comment may interrupt once this hits 0
    without_interception = MIN_CONVO_LEN
    # min_len counts the start, so every conversation has at least MIN_CONVO_LEN sampled Comments
    new_convo = lambda start=None: SampledConversation(
        app_state.starters, app_state.comments, start, min_len=MIN_CONVO_LEN + 1
    )
    convo = iter(())  # empty: the first step opens a conversation like every later one
    repeat_as_comment = None  # sent right after a conversation starter or a new comment
    is_first = True  # the first message since startup
    while True:
        is_new = without_interception < 1 and not queue.empty()
        if repeat_as_comment is not None:
            # the starter/new comment sent last is repeated once as a Comment, before anything else
            cur, repeat_as_comment, is_new = repeat_as_comment, None, False
        elif is_new:
            cur = queue.get_nowait()
            
            without_interception = MIN_CONVO_LEN
            # `cur` is already stored: the receiver added it via app_state.comments.add
            
            # here, this comment starts its own conversation together with its parent
            # (if there is one)
            convo = new_convo(cur)  # TODO: include parent
            next(convo)  # skip `cur` itself (the convo's start): it is repeated via repeat_as_comment
            repeat_as_comment = cur.to_Comment()
        else:
            try:
                cur = next(convo)
            except StopIteration:
                convo = new_convo()
                cur = next(convo)
                repeat_as_comment = cur.to_Comment()
                without_interception = MIN_CONVO_LEN  # the starter doesn't count
            else:
                without_interception -= 1
                

        # the first message after startup (a starter) waits INTERVAL like a Comment
        long_wait = is_new or (isinstance(cur, ConversationStarter) and not is_first)
        wait = LONG_INTERVAL if long_wait else INTERVAL
        is_first = False
        msg = render(cur, next_slot(app_state), new=is_new)
        app_state.last_msg = msg.model_dump(mode="json")
        await broadcast(app_state, app_state.last_msg)

        # sleep after sending, so the first message goes out right at startup
        next_at += wait
        await asyncio.sleep(max(0, next_at - loop.time()))



@app.websocket("/ws")
async def main(websocket: WebSocket):
    await websocket.accept()
    state = websocket.app.state
    # Catch the new client up with the message currently on screen instead of making it
    # wait for the next tick. If a broadcast happens while we send, send the newer one
    # too; only then join the broadcast set (no await in between, so nothing is missed).
    sent = None
    while state.last_msg is not sent:
        sent = state.last_msg
        await websocket.send_json(sent)
    connections = state.connections
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
