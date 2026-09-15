"""Outgoing payloads: what the client receives for each kind of statement."""

from __future__ import annotations

from pydantic import BaseModel

from app.models.fields import CommentText, Slot
from app.models.statements import Comment, ConversationStarter


class StatementRender(BaseModel):
    ID: int  # passed to the frontend, so it can pass it back e.g. for flagging
    text: CommentText


class CommentRender(StatementRender):
    slot: Slot
    is_flagged: bool  # flagged comments may be blurred to be illegible

    @classmethod
    def from_Comment(cls, comment: Comment, slot: Slot):
        return cls(ID=comment.ID, text=comment.text, slot=slot,
                   is_flagged=(comment.flag is not None))


class NewCommentRender(StatementRender):
    @classmethod
    def from_Comment(cls, comment: Comment):
        return cls(ID=comment.ID, text=comment.text)


class ConversationStarterRender(StatementRender):
    @classmethod
    def from_ConversationStarter(cls, conv_starter: ConversationStarter):
        return cls(ID=conv_starter.ID, text=conv_starter.text)
