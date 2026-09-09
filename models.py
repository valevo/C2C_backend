from __future__ import annotations
from itertools import count
from datetime import datetime
from enum import Enum
from typing import ClassVar, Literal, Union, Annotated, get_args, Tuple
from pydantic import BaseModel, Field, create_model, TypeAdapter

# import numpy as np
import yaml

_id_counter: ClassVar[count] = count(1)
Comment_ID = Annotated[int, Field(default_factory=lambda: next(_id_counter))]

CommentText = Annotated[str, Field(description="Comment body, plain text")]

ReplyTo = Annotated[Union[int, None], 
                Field(description="The ID of the comment that this comment is a reply to. None (null) if it is not a reply to any comment. The presence of this property distinguishes comments created by clicking on an existing comment vs using the 'ik wil iets anders zeggen' button.")
    ]

TimeStamp = Annotated[datetime, Field(default_factory=datetime.now)]

Slot = Annotated[
    Literal["top", "bottom"],
    Field(description="Where to render the comment, either in the top field or the bottom field."),
]

#################################################
##### BASICS
#################################################

with open("messages.yaml", encoding="utf-8") as f:
    MESSAGES = yaml.safe_load(f)

topics = MESSAGES["new_comment"]["topics"]
# print(topics)
Topic = Annotated[Enum("Topic", {k: k for k in topics}, type=str),
            Field(description="The name of a topic chosen for a comment.")]


LanguageCodeValues = Literal["en", "nl", "fr"]

LanguageCode = Annotated[
    LanguageCodeValues,
    Field(default="en", description="ISO 639-1 code of the language chosen by the user."),
]

SUPPORTED_LANGUAGES: tuple[str] = get_args(LanguageCodeValues)  # ("en", "nl", "fr")


class Statement(BaseModel):
    """
    Base class for Comment, ConversationStarter(, NewComment (if that's ever needed)).
    """
    ID: Comment_ID
    text: CommentText
    language: LanguageCode
    timestamp: TimeStamp


class StatementRender(BaseModel):
    ID: int
    text: CommentText



#################################################
##### FLAG
#################################################

class FlagCreate(BaseModel):
    reason: str
    donotshow: bool
    comment_ID: int # this needs verification that the comment's ID actually exists


class Flag(FlagCreate):
    # comment_ID: int
    # author_ID: AuthorID
    timestamp: TimeStamp


#################################################
##### COMMENT
#################################################


class CommentCreate(BaseModel):
    text: CommentText
    reply_to: ReplyTo = None


class Comment(CommentCreate, Statement):
    # _id_counter: ClassVar[count] = count(1)
    # ID: int = Field(default_factory=lambda: next(Comment._id_counter)) -> part of the parent class Statement now
    # author_ID: AuthorID -> we skip author_ID because it would just be the connection's ID
    flag: Flag = None

    def __repr__(self):
        return f'Comment("{self.text}")'
Comment.model_rebuild()  # resolves the forward reference to Comment


class CommentRender(StatementRender):
    slot: Slot
    is_flagged: bool

    @classmethod
    def from_Comment(cls, comment: Comment, slot: Slot):
        return cls(ID=comment.ID, text=comment.text, slot=slot,
                   is_flagged=(comment.flag is not None))



class NewCommentRender(StatementRender):
    # slot: Slot = Field(default="top", exclude=True)
    # is_flagged: bool = Field(default=False, exclude=True)
    
    @classmethod
    def from_Comment(cls, comment: Comment):
        return cls(ID=comment.ID, text=comment.text) 


#################################################
##### ConversationStarter
#################################################


class ConversationStarter(Statement):
    topics: tuple[Topic, ...]


class ConversationStarterRender(StatementRender):
    # ID: Comment_ID
    # text: CommentText

    @classmethod
    def from_ConversationStarter(cls, conv_starter: ConversationStarter):
        return cls(ID=conv_starter.ID, text=conv_starter.text)




#################################################
##### HELPERS -- OUTGOING
#################################################

def make_message(type_tag: str, payload_model: Type[BaseModel]) -> Type[BaseModel]:
    return create_model(
        f"{payload_model.__name__}Message",
        type=(Literal[type_tag], type_tag),
        payload=(payload_model, ...),
    )


    
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


#################################################
##### HELPERS -- INCOMING
#################################################




CommentCreateMessage = make_message("comment", CommentCreate)
FlagCreateMessage = make_message("flag", FlagCreate)
# ConversationStarterCreateMessage = make_message("conversation_starter",
#                                                 ConversationStarterCreate)



IncomingMessage = Annotated[
    Union[CommentCreateMessage, FlagCreateMessage],
    Field(discriminator="type"),
]

incoming_adapter = TypeAdapter(IncomingMessage)


#################################################
##### OTHER: STATIC CONTENT, SMALL MESSAGES
#################################################


class LanguageChoice(BaseModel):
    language: LanguageCode





