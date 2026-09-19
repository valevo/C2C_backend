"""Sampling of short "conversations" to broadcast when no new comment is waiting."""

import random
from collections.abc import Iterator

from app.data import Comments, ConversationStarters
from app.models import Statement


class SampledConversation(Iterator[Statement]):
    """Iterator over one conversation: a start statement followed by random comments.

    The conversation always yields at least `min_len` statements (counting the start).
    After that, each step continues with probability `continue_prob` and otherwise stops.
    When no `start` is given, a random conversation starter opens the conversation. A
    freshly received comment can be passed as `start` so it opens its own conversation.

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
        self.start = start if start is not None else starters.random(self.rng)
        self.cur: Statement = self.start
        self.emitted = 0

    def sample_next(self) -> Statement:
        """Pick the statement to follow the current one."""
        # TODO: prefer replies to / the parent of the current statement over any comment
        if len(self.comments) == 0:
            return self.starters.random(self.rng)
        return self.comments.random(self.rng)

    def __next__(self) -> Statement:
        if self.emitted < self.min_len or self.rng.random() < self.continue_prob:
            to_return = self.cur
            self.cur = self.sample_next()
            self.emitted += 1
            return to_return
        raise StopIteration
