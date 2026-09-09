"""Wire envelopes: {"type": <tag>, "payload": {...}} in both directions."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, create_model

from app.models.fields import Slot
from app.models.render import CommentRender, ConversationStarterRender, NewCommentRender
from app.models.statements import Comment, CommentCreate, ConversationStarter, FlagCreate, Statement


def make_message(type_tag: str, payload_model: type[BaseModel]) -> type[BaseModel]:
    return create_model(
        f"{payload_model.__name__}Message",
        type=(Literal[type_tag], type_tag),
        payload=(payload_model, ...),
    )


#################################################
##### INCOMING (client -> server)
#################################################

CommentCreateMessage = make_message("comment", CommentCreate)
FlagCreateMessage = make_message("flag", FlagCreate)
# ConversationStarterCreateMessage = make_message("conversation_starter", ConversationStarterCreate)

IncomingMessage = Annotated[
    Union[CommentCreateMessage, FlagCreateMessage],
    Field(discriminator="type"),
]

incoming_adapter = TypeAdapter(IncomingMessage)


#################################################
##### OUTGOING (server -> client)
#################################################

CommentRenderMessage = make_message("comment", CommentRender)
NewCommentRenderMessage = make_message("new_comment", NewCommentRender)
ConversationStarterRenderMessage = make_message("conversation_starter", ConversationStarterRender)

OutgoingMessage = Annotated[
    Union[CommentRenderMessage, NewCommentRenderMessage, ConversationStarterRenderMessage],
    Field(discriminator="type"),
]


def render(statement: Statement, slot: Slot, new: bool = False) -> BaseModel:
    """Wrap a DB entry in the outgoing envelope matching its class."""
    match statement:
        case ConversationStarter():
            return ConversationStarterRenderMessage(
                payload=ConversationStarterRender.from_ConversationStarter(statement))
        case Comment() if new:
            return NewCommentRenderMessage(payload=NewCommentRender.from_Comment(statement))
        case Comment():
            return CommentRenderMessage(payload=CommentRender.from_Comment(statement, slot))
    raise TypeError(f"no render for {type(statement).__name__}")
