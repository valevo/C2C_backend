"""Domain models: what is stored in the DB and what clients may create."""

from __future__ import annotations

from pydantic import BaseModel

from app.models.fields import Comment_ID, CommentText, LanguageCode, ReplyTo, TimeStamp, Topic


class Statement(BaseModel):
    """Base class for Comment, ConversationStarter(, NewComment (if that's ever needed))."""
    ID: Comment_ID
    text: CommentText
    language: LanguageCode
    timestamp: TimeStamp


#################################################
##### FLAG
#################################################

class FlagCreate(BaseModel):
    reason: str
    donotshow: bool
    comment_ID: int  # this needs verification that the comment's ID actually exists


class Flag(FlagCreate):
    # author_ID is skipped because it would just be the connection's ID
    timestamp: TimeStamp


#################################################
##### COMMENT
#################################################

class CommentCreate(BaseModel):
    text: CommentText
    reply_to: ReplyTo = None


class Comment(CommentCreate, Statement):
    flag: Flag | None = None

    def __repr__(self):
        return f'Comment("{self.text}")'


#################################################
##### ConversationStarter
#################################################

class ConversationStarter(Statement):
    topics: tuple[Topic, ...]


#################################################
##### OTHER: SMALL MESSAGES
#################################################

class LanguageChoice(BaseModel):
    language: LanguageCode
