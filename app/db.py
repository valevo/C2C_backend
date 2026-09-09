"""In-memory database. Replace with a real store when needed."""

from app.models import Comment, ConversationStarter, Statement


def seed_DB() -> dict[int, Statement]:
    starter = ConversationStarter(text="i think", topics=["what_is_democracy"])
    init_comments = [
        starter,
        Comment(text="hi", reply_to=starter.ID),
        Comment(text="bye"),
    ]
    return {c.ID: c for c in init_comments}
