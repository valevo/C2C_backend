"""Sampling of short "conversations" to broadcast when no new comment is waiting."""

import random
from collections.abc import Iterator

from app.data import Comments, ConversationStarters
from app.models import Comment, ConversationStarter, Statement


class SampledConversation(Iterator[Statement]):
    """Iterator over one conversation: a start statement followed by random replies.

    The replies are sampleable (verified, unflagged) comments in the thread of the
    conversation's starter (direct replies and replies to those), each shown at most once.
    The conversation yields at least `min_len` statements (counting the start) as long as
    unshown replies remain; after
    that, each step continues with probability `continue_prob` and otherwise stops.
    When no `start` is given, a random conversation starter with sampleable replies opens the
    conversation. A freshly received comment can be passed as `start` so it opens its own
    conversation, continued by the thread of the starter it (indirectly) replies to.

    Usage:
        convo = SampledConversation(starters, comments)
        for statement in convo: ...
    """

    def __init__(
        self,
        starters: ConversationStarters,
        comments: Comments,
        start: Statement | None = None,
        min_len: int = 4,
        continue_prob: float = 0.5,
        rng: random.Random | None = None,
    ):
        self.starters = starters
        self.comments = comments
        self.min_len = min_len
        self.continue_prob = continue_prob
        self.rng = rng or random.Random()
        self.start = start if start is not None else self.random_starter()
        self.starter = self.starter_of(self.start)
        self.cur: Statement | None = self.start
        self.shown: set[int] = set()
        self.emitted = 0

    def random_starter(self) -> ConversationStarter:
        """A random starter with at least one sampleable reply (any starter if none has one)."""
        replied_to = {c.reply_to for c in self.comments.sampleable}
        with_replies = self.starters._new(s for s in self.starters if s.ID in replied_to)
        return (with_replies or self.starters).random(self.rng)

    def starter_of(self, statement: Statement) -> ConversationStarter | None:
        """The starter at the root of `statement`'s reply chain (None for a top-level comment)."""
        seen: set[int] = set()
        while isinstance(statement, Comment) and statement.reply_to is not None:
            if statement.ID in seen:  # guard against reply cycles
                return None
            seen.add(statement.ID)
            parent = self.comments.get(statement.reply_to) or self.starters.get(statement.reply_to)
            if parent is None:
                return None
            statement = parent
        return statement if isinstance(statement, ConversationStarter) else None

    def thread(self) -> list[Comment]:
        """All comments replying, directly or indirectly, to this conversation's starter."""
        if self.starter is None:
            return []
        in_thread = {self.starter.ID}
        # comments are stored in arrival order, so a parent precedes its replies
        found = []
        for c in self.comments:
            if c.reply_to in in_thread:
                in_thread.add(c.ID)
                found.append(c)
        return found

    def sample_next(self) -> Statement | None:
        """Pick a sampleable, not yet shown reply from the starter's thread; None when none is left."""
        pool = [
            c for c in self.thread()
            if c.verified and c.flag is None and c.ID not in self.shown
        ]
        return self.rng.choice(pool) if pool else None

    def __next__(self) -> Statement:
        if self.cur is not None and (
            self.emitted < self.min_len or self.rng.random() < self.continue_prob
        ):
            to_return = self.cur
            self.shown.add(to_return.ID)
            self.cur = self.sample_next()
            self.emitted += 1
            return to_return
        raise StopIteration
