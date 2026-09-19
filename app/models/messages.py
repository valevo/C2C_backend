"""Wire envelopes: {"type": <tag>, "payload": {...}} in both directions."""

from __future__ import annotations

import random
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, create_model

from app.models.fields import CommentText, Slot, Language
# from app.models.render import CommentRender, ConversationStarterRender, NewCommentRender
from app.models.statements import Comment, CommentCreate, ConversationStarter, FlagCreate, Statement


def make_message(type_tag: str, payload_model: type[BaseModel]) -> type[BaseModel]:
    return create_model(
        f"{payload_model.__name__}Message",
        type=(Literal[type_tag], type_tag),
        payload=(payload_model, ...)
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

class StatementRender(BaseModel):
    ID: int  # passed to the frontend, so it can pass it back e.g. for flagging
    text: CommentText
    language: Language


class CommentRender(StatementRender):
    slot: Slot
    is_flagged: bool  # flagged comments may be blurred to be illegible

    @classmethod
    def from_Comment(cls, comment: Comment, slot: Slot):
        return cls(ID=comment.ID, text=comment.text, slot=slot,
                   is_flagged=(comment.flag is not None),
                  language=comment.language)


class NewCommentRender(StatementRender):
    @classmethod
    def from_Comment(cls, comment: Comment):
        return cls(ID=comment.ID, text=comment.text,
                  language=comment.language)


class ConversationStarterTextRender(StatementRender):
    slot: Slot

    @classmethod
    def from_ConversationStarter(
        cls,
        conv_starter: ConversationStarter,
        slot: Slot,
        language: Language | None = None,
        text: str | None = None,
    ):
        """Render one language's text of `conv_starter` in `slot`.

        Defaults to the starter's own text/language (used for the "top" slot); pass
        `language`/`text` together to render a translation instead (the "bottom" slot).
        """
        return cls(
            ID=conv_starter.ID,
            text=conv_starter.text if text is None else text,
            slot=slot,
            language=conv_starter.language if language is None else language,
        )


def _pick_translation_language(language: Language, rng: random.Random | None = None) -> Language:
    """Which language the "bottom" slot shows, given the starter's own ("top") language.

    A Dutch starter is paired with a random choice of English or French; any other
    starter is paired with Dutch.
    """
    if language == "nl":
        return (rng or random).choice(["en", "fr"])
    return "nl"


class ConversationStarterRender(BaseModel):
    starter: ConversationStarterTextRender
    translation: ConversationStarterTextRender

    @classmethod
    def from_ConversationStarter(cls, conv_starter: ConversationStarter, rng: random.Random | None = None):
        starter_render = ConversationStarterTextRender.from_ConversationStarter(conv_starter, slot="top")

        transl_language = _pick_translation_language(conv_starter.language, rng)
        transl_text = conv_starter.translations.get(transl_language)
        if transl_text is None:
            # no translation available (e.g. a starter built without one, as in tests):
            # fall back to the starter's own text/language rather than mislabeling it.
            transl_language, transl_text = conv_starter.language, conv_starter.text
        transl_render = ConversationStarterTextRender.from_ConversationStarter(
            conv_starter, slot="bottom", language=transl_language, text=transl_text,
        )

        return cls(starter=starter_render, translation=transl_render)



def make_message_outgoing(type_tag: str, payload_model: type[BaseModel]) -> type[BaseModel]:
    return create_model(
        f"{payload_model.__name__}Message",
        type=(Literal[type_tag], type_tag),
        payload=(payload_model, ...),
        display_seconds=float
    )
    
CommentRenderMessage = make_message_outgoing("comment", CommentRender)
NewCommentRenderMessage = make_message_outgoing("new_comment", NewCommentRender)
ConversationStarterRenderMessage = make_message_outgoing("conversation_starter", ConversationStarterRender)

OutgoingMessage = Annotated[
    Union[CommentRenderMessage, NewCommentRenderMessage, ConversationStarterRenderMessage],
    Field(discriminator="type"),
]



def render(statement: Statement, slot: Slot, new: bool = False) -> BaseModel:
    """Wrap a DB entry in the outgoing envelope matching its class."""
    l = len(statement.text)
    normed = (l - 0)/(200 - 0)  # 0 and 200 are theoretical limits
    display_seconds = (normed * 3) + 1 # implies that length will be between 1 and 4
    
    match statement:
        case ConversationStarter():
            return ConversationStarterRenderMessage(
                payload=ConversationStarterRender.from_ConversationStarter(statement),
                display_seconds=display_seconds
            )
        case Comment() if new:
            return NewCommentRenderMessage(payload=NewCommentRender.from_Comment(statement),
                display_seconds=display_seconds
            )
        case Comment():
            return CommentRenderMessage(payload=CommentRender.from_Comment(statement, slot),
                display_seconds=display_seconds
            )
    raise TypeError(f"no render for {type(statement).__name__}")
    