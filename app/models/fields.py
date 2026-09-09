"""Shared field types (Annotated aliases) used across the models."""

from __future__ import annotations

from datetime import datetime
from itertools import count
from typing import Annotated, Literal, Union, get_args

from pydantic import Field

from app.i18n import Language, Topic as TopicEnum

_id_counter = count(1)

Comment_ID = Annotated[int, Field(default_factory=lambda: next(_id_counter))]

CommentText = Annotated[str, Field(description="Comment body, plain text")]

ReplyTo = Annotated[
    Union[int, None],
    Field(description=(
        "The ID of the comment that this comment is a reply to. None (null) if it is not a reply "
        "to any comment. The presence of this property distinguishes comments created by clicking "
        "on an existing comment vs using the 'ik wil iets anders zeggen' button."
    )),
]

TimeStamp = Annotated[datetime, Field(default_factory=datetime.now)]

Slot = Annotated[
    Literal["top", "bottom"],
    Field(description="Where to render the comment, either in the top field or the bottom field."),
]

Topic = Annotated[TopicEnum, Field(description="The name of a topic chosen for a comment.")]

LanguageCode = Annotated[
    Language,
    Field(default="en", description="ISO 639-1 code of the language chosen by the user."),
]

SUPPORTED_LANGUAGES: tuple[str, ...] = get_args(Language)  # ("en", "nl", "fr")
