"""The /ws route and the per-connection receive loop."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.models import Comment, CommentCreateMessage, Flag, FlagCreateMessage, incoming_adapter
from app.ws.manager import mgr

router = APIRouter()


async def receiver_loop(websocket: WebSocket, anon_id: str) -> None:
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
                comment = Comment(**comment_create.model_dump())
                state.DB[comment.ID] = comment
                state.new_comments.put_nowait(comment)
            case FlagCreateMessage(payload=flag_create):
                flag = Flag(**flag_create.model_dump())
                state.DB[flag.comment_ID].flag = flag


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    anon_id = await mgr.connect(websocket)
    # data = await websocket.receive_json()  # this would consume the LanguageChoice
    try:
        await receiver_loop(websocket, anon_id)
    finally:
        mgr.disconnect(websocket)
