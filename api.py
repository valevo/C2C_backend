# HARDCODED IN FRONT-END
#
# - fade time -> BUT: backend sends new comment, so that sets the pace...?
# - blur radius for CommentRender -> is_flagged signals it to front-end
# - inverted colours for NewCommentRender & for ConversationStarter -> signalled by different types (?)
# - 



# TBD
#
# - should the flag symbol of a flagged comment change (e.g. be filled)? if so, does the backend need to communicate that explicitly or is enough that CommentRender.is_flagged == True 
# - time to replace comment with new one is fixed, so fade timer is known in advance?
# - CommentRender needs a language code, right? -> are there 
#



from typing import Dict, Literal
from pydantic import ValidationError

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager
import asyncio

import uuid
import json
from datetime import datetime

import random


from models import CommentCreate, FlagCreate
from models import Comment, Flag, ConversationStarter
from models import CommentRender
from models import CommentCreateMessage, FlagCreateMessage
from models import incoming_adapter, IncomingMessage
from models import render, OutgoingMessage   # OutgoingMessage only needed for docs/AsyncAPI





def seed_DB():
    starter = ConversationStarter(text="i think", topics=["what_is_democracy"])
    init_comments = [
        starter,
        Comment(text="hi", reply_to=starter.ID), 
        Comment(text="bye")]
    return {c.ID: c for c in init_comments}



# async def sender_loop(app: FastAPI):
#     while True:
#         cur = np.random.choice(list(app.state.DB.values()))
#         app.state.current_comment = cur
#         render_cls = CommentRender
#         render = CommentRender.from_Comment(cur, app.state.current_slot)
#         app.state.current_slot = "bottom" if app.state.current_slot == "top" else "top"
#         msg = render.model_dump(mode="json")
#         await mgr.broadcast(msg)
#         await asyncio.sleep(render.fade_time)  # np.random.randint(3, 7))

def generate_anonymous_id() -> str:
    """Generate an anonymous ID based on uuid4, e.g. 'Anon-3f9a2c1e'."""
    return f"anonymous-{uuid.uuid4().hex[:8]}"


class Manager:
    """Tracks active WebSocket connections and their anonymous IDs."""
    def __init__(self) -> None:
        self.active_connections: Dict[WebSocket, str] = {}
    
    
    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        # uuid4 is random and collision-proof for practical purposes, so no
        # need to check against existing IDs.
        anon_id = generate_anonymous_id()
        self.active_connections[websocket] = anon_id
        return anon_id

    def disconnect(self, websocket: WebSocket) -> str | None:
        return self.active_connections.pop(websocket, None)

    def user_list(self) -> list[str]:
        return list(self.active_connections.values())

    async def broadcast(self, message: dict) -> None:
        stale = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)



def next_slot(app: FastAPI) -> str:
    slot = app.state.current_slot
    app.state.current_slot = "bottom" if slot == "top" else "top"
    return slot


async def sender_loop(app: FastAPI):
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.DB = seed_DB()
    app.state.current_slot = "top"
    app.state.new_comments: asyncio.Queue[Comment] = asyncio.Queue()
    for c in app.state.DB.values():
        app.state.new_comments.put_nowait(c)
    sender = asyncio.create_task(sender_loop(app))
    sender.add_done_callback(lambda t: t.cancelled() or t.exception() and t.print_stack())
    yield
    sender.cancel()


app = FastAPI(title="Comment2Conversation", version="0.1", lifespan=lifespan)

mgr = Manager()


async def receiver_loop(websocket: WebSocket, anon_id: str):
    """Runs independently, reacting to incoming messages whenever they arrive."""
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
                comment = Comment(**comment_create.model_dump())
                app.state.DB[comment.ID] = comment
                app.state.new_comments.put_nowait(comment)
            case FlagCreateMessage(payload=flag_create):
                flag = Flag(**flag_create.model_dump())
                app.state.DB[flag.comment_ID].flag = flag



@app.websocket("/ws")
async def main(websocket: WebSocket):
    anon_id = await mgr.connect(websocket)
    # data = await websocket.receive_json() # this will consume the LanguageChoice
    try:
        await receiver_loop(websocket, anon_id)
    finally:
        mgr.disconnect(websocket)


# @app.websocket("/ws")
# async def main(websocket: WebSocket):
#     anon_id = await mgr.connect(websocket)
    
    
#     receive_task = asyncio.create_task(receiver_loop(websocket, anon_id))
#     send_task = asyncio.create_task(sender_loop(websocket))
#     try:
#         done, pending = await asyncio.wait(
#             {receive_task, send_task}, return_when=asyncio.FIRST_COMPLETED
#         )
#         for task in pending:
#             task.cancel()
#         for task in done:
#             task.result()
#     finally:
#         mgr.disconnect(websocket)
    



@app.get("/db")
def get_DB():
    ls = [c.model_dump(mode="json") for c in app.state.DB.values()]
    return ls

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)