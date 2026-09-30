from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.models.fields import Comment_ID, CommentText,\
            LanguageCode, ReplyTo, TimeStamp, Topic


class Statement(BaseModel):
    """Base class for Comment, ConversationStarter(, NewComment (if that's ever needed))."""
    ID: Comment_ID
    text: CommentText
    language: LanguageCode
    timestamp: TimeStamp
    topics: tuple[Topic, ...]



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
    # the flag's row in data/flags.csv; None until it is stored there
    flag_id: int | None = None


#################################################
##### COMMENT
#################################################

class CommentCreate(BaseModel):
    """Create model for a Comment that isn't a reply, i.e. added via 'I want to say something else'"""
    text: CommentText
    reply_to: ReplyTo | None
    topics: tuple[Topic, ...] | None

    @model_validator(mode="after")
    def check_exactly_one(self) -> "CommentCreate":
        has_reply = self.reply_to is not None
        has_topics = bool(self.topics)
        if has_reply == has_topics:  # both True or both False
            raise ValueError("must provide exactly one of 'reply_to' or 'topics'")
        return self


class Comment(CommentCreate, Statement):
    # topics: tuple[Topic, ...] # this would be here to make topics non-optional
    flag: Flag | None = None
    # only verified (and unflagged) comments are sampled into conversations; new comments
    # start unverified and are verified by hand in the CSV they're stored in
    verified: bool = False

    # @model_validator(mode="after")
    # def inherit_topics_from_reply(self) -> "Comment":
    #     if self.topics is None:
    #         parent = find_comment_by_id(self.reply_to)  # lookup in DB
    #         self.topics = parent.topics
    #     return self

    @model_validator(mode="after")
    def check_exactly_one(self) -> "CommentCreate":
        return self

    def to_Comment(self) -> Comment:
        """This comment as a Comment, i.e. itself (counterpart of ConversationStarter.to_Comment).

        Returns `self` rather than a copy, so a flag set on the stored comment
        in the meantime still shows when it is rendered.
        """
        return self

    # def __init__(self, 
    def __repr__(self):
        return f'Comment("{self.text}")'


#################################################
##### ConversationStarter
#################################################

class ConversationStarter(Statement):
    # topics: tuple[Topic, ...]

    # This starter's full text in other languages, keyed by ISO 639-1 code (e.g. {"en": "...",
    # "fr": "..."}); does not need to include its own `language`. Populated by the CSV loader
    # from the translated survey export; empty for starters built without translations
    # (e.g. in tests), in which case renders fall back to the starter's own text/language.
    translations: dict[str, str] = Field(default_factory=dict)

    def to_Comment(self) -> Comment:
        """This starter as a top-level Comment (same ID, text, language, timestamp and topics).

        The translations are dropped; the Comment is unflagged and not a reply.
        """
        return Comment(
            ID=self.ID, text=self.text, language=self.language,
            timestamp=self.timestamp, topics=self.topics, reply_to=None,
        )


#################################################
##### OTHER: SMALL MESSAGES
#################################################

class LanguageChoice(BaseModel):
    language: LanguageCode
