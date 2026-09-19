"""In-memory database. Replace with a real store when needed."""

import json
import random
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generic, TypeVar

import pandas as pd

from app.models import Comment, CommentCreate, ConversationStarter, Statement, Topic

# <project root>/data/conversation_starters/
CONVERSATION_STARTERS_DIR = Path(__file__).resolve().parents[1] / "data" / "conversation_starters"
CONVERSATION_STARTERS_CSV = CONVERSATION_STARTERS_DIR / "conversation_starters_20260821_topics.csv"
CONVERSATION_STARTERS_TRANSLATIONS_CSV = (
    CONVERSATION_STARTERS_DIR / "conversation_starters_20260821_translated.csv"
)

_CSV_TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M:%S"
_AFFIRMATIVE = {"ja", "yes", "oui"}


def read_conversation_starters_csv(path: Path = CONVERSATION_STARTERS_CSV) -> pd.DataFrame:
    """Read the survey export CSV into a DataFrame.

    Columns: timestamp (parsed to datetime), text, permission_processing, permission_sharing,
    language_code, topics (a JSON-encoded list of topic IDs, parsed to a tuple of Topic).
    The leading unnamed column of the export is used as the index.
    """
    df = pd.read_csv(path, index_col=0, encoding="utf-8")
    df["timestamp"] = pd.to_datetime(df["timestamp"], format=_CSV_TIMESTAMP_FORMAT)
    for col in ("text", "permission_processing", "permission_sharing", "language_code"):
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["language_code"] = df["language_code"].str.lower()
    df["topics"] = df["topics"].apply(_parse_topics)
    return df


def _parse_topics(value) -> tuple[Topic, ...]:
    """Parse a JSON-encoded list of topic IDs; empty/missing cells yield an empty tuple."""
    if pd.isna(value) or not str(value).strip():
        return ()
    return tuple(Topic(name) for name in json.loads(value))


def read_translations_csv(
    path: Path = CONVERSATION_STARTERS_TRANSLATIONS_CSV,
) -> dict[datetime, dict[str, str]]:
    """Read the translated-starters CSV into {timestamp: {language_code: text}}.

    That CSV shares its columns with the main survey export, but has three rows per
    starter (one per language) sharing the same timestamp; grouping by timestamp
    recovers each starter's translations.
    """
    df = pd.read_csv(path, index_col=0, encoding="utf-8")
    df["timestamp"] = pd.to_datetime(df["timestamp"], format=_CSV_TIMESTAMP_FORMAT)
    df["language_code"] = df["language_code"].fillna("").astype(str).str.strip().str.lower()
    df["text"] = df["text"].fillna("").astype(str)
    translations: dict[datetime, dict[str, str]] = {}
    for row in df.itertuples(index=False):
        translations.setdefault(row.timestamp.to_pydatetime(), {})[row.language_code] = row.text
    return translations


def load_conversation_starters(
    path: Path = CONVERSATION_STARTERS_CSV,
    require_permission: bool = True,
    translations_path: Path | None = CONVERSATION_STARTERS_TRANSLATIONS_CSV,
) -> list[ConversationStarter]:
    """Build ConversationStarters from the survey export CSV.

    Each starter's topics come from the CSV's `topics` column.
    With `require_permission`, rows lacking an affirmative answer ("Ja"/"Yes") in both
    permission columns are skipped.
    If `translations_path` is given, each starter's `translations` is populated from it
    (matched to `path`'s rows by shared timestamp); pass None to skip, leaving every
    starter's `translations` empty.
    """
    df = read_conversation_starters_csv(path)
    df = df[df["text"] != ""]
    if require_permission:
        df = df[
            df["permission_processing"].str.lower().isin(_AFFIRMATIVE)
            & df["permission_sharing"].str.lower().isin(_AFFIRMATIVE)
        ]
    translations_by_timestamp = (
        read_translations_csv(translations_path) if translations_path is not None else {}
    )
    return [
        ConversationStarter(
            text=row.text,
            language=row.language_code,
            timestamp=row.timestamp.to_pydatetime(),
            topics=row.topics,
            translations=translations_by_timestamp.get(row.timestamp.to_pydatetime(), {}),
        )
        for row in df.itertuples(index=False)
    ]


S = TypeVar("S", bound=Statement)


class Statements(Sequence[S], Generic[S]):
    """Collection of Statements with lookup, filtering and sampling.

    Behaves like a sequence: `len(c)`, `c[i]`, `for s in c`, `s in c`. The base class has
    no mutators; subclasses whose contents change over time (see `Comments`) add them.
    Filtering methods return a new collection of the same concrete class, so
    subclass-specific helpers remain available after filtering.
    """

    def __init__(self, statements: Iterable[S] = ()):
        self._items: list[S] = []
        self._by_id: dict[int, S] = {}
        for statement in statements:
            self._append(statement)

    def _append(self, statement: S) -> None:
        """Append one statement, rejecting duplicate IDs (shared by __init__ and mutators)."""
        if statement.ID in self._by_id:
            raise ValueError(f"duplicate statement ID {statement.ID}")
        self._items.append(statement)
        self._by_id[statement.ID] = statement

    # --- Sequence protocol -------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]

    def __iter__(self) -> Iterator[S]:
        return iter(self._items)

    def __contains__(self, item: object) -> bool:
        return item in self._items

    def __repr__(self) -> str:
        return f"{type(self).__name__}(n={len(self)})"

    def _new(self, statements: Iterable[S]):
        """Build a collection of the same concrete class (keeps subclass helpers)."""
        return type(self)(statements)

    # --- Lookup ------------------------------------------------------------

    @property
    def ids(self) -> tuple[int, ...]:
        return tuple(self._by_id)

    def get(self, ID: int) -> S | None:
        """Return the statement with this ID, or None."""
        return self._by_id.get(ID)

    def __call__(self, ID: int) -> S:
        """Return the statement with this ID; raise KeyError if unknown."""
        return self._by_id[ID]

    @property
    def languages(self) -> frozenset[str]:
        return frozenset(s.language for s in self._items)

    @property
    def topics(self) -> frozenset[Topic]:
        return frozenset(t for s in self._items for t in s.topics)

    # --- Filtering ---------------------------------------------------------

    def filter(
        self,
        language: str | None = None,
        topic: Topic | None = None,
        topics: Iterable[Topic] | None = None,
    ):
        """Return a new collection restricted by language and/or topic.

        `topic` keeps statements tagged with that topic; `topics` keeps statements tagged
        with any of the given topics. Filters combine with AND.
        """
        wanted = set(topics or ())
        if topic is not None:
            wanted.add(topic)
        return self._new(
            s for s in self._items
            if (language is None or s.language == language)
            and (not wanted or wanted.intersection(s.topics))
        )

    def by_topic(self) -> dict[Topic, "Statements[S]"]:
        """Group statements by topic; a statement with several topics appears in each group."""
        groups: dict[Topic, list[S]] = {}
        for s in self._items:
            for t in s.topics:
                groups.setdefault(t, []).append(s)
        return {t: self._new(lst) for t, lst in groups.items()}

    def by_language(self) -> dict[str, "Statements[S]"]:
        groups: dict[str, list[S]] = {}
        for s in self._items:
            groups.setdefault(s.language, []).append(s)
        return {lang: self._new(lst) for lang, lst in groups.items()}

    # --- Sampling ----------------------------------------------------------

    def random(self, rng: random.Random | None = None) -> S:
        """Return one uniformly random statement; raise IndexError if empty."""
        if not self._items:
            raise IndexError(f"no items to sample from in {self!r}")
        return (rng or random).choice(self._items)


class ConversationStarters(Statements[ConversationStarter]):
    """Static (read-only) collection of ConversationStarters, normally loaded from the survey CSV."""

    @classmethod
    def from_csv(
        cls,
        path: Path = CONVERSATION_STARTERS_CSV,
        require_permission: bool = True,
        translations_path: Path | None = CONVERSATION_STARTERS_TRANSLATIONS_CSV,
    ) -> "ConversationStarters":
        return cls(load_conversation_starters(
            path, require_permission=require_permission, translations_path=translations_path,
        ))





class Comments(Statements[Comment]):
    """Growing collection of Comments: new ones are added as they arrive over the websocket.

    A comment without topics inherits them from the statement it replies to, which is
    looked up among the comments already present and, if given, in `starters`.
    Adds reply-thread helpers on top of the shared functionality.
    """

    def __init__(
        self,
        comments: Iterable[Comment] = (),
        starters: Statements[ConversationStarter] | None = None,
    ):
        self._starters = starters
        super().__init__(comments)

    def _new(self, comments: Iterable[Comment]) -> "Comments":
        return Comments(comments, starters=self._starters)

    # --- Mutators ----------------------------------------------------------

    def add(self, comment: Comment | CommentCreate) -> Comment:
        """Append a new comment and return it (as a Comment).

        A CommentCreate (as received over the websocket) is turned into a Comment first.
        Missing topics are inherited from the parent; raises ValueError if the comment has
        neither topics nor a resolvable parent, or if its ID is already present.
        """
        if not isinstance(comment, Comment):
            comment = Comment(**comment.model_dump())
        self._append(comment)
        return comment

    def extend(self, comments: Iterable[Comment | CommentCreate]) -> None:
        for comment in comments:
            self.add(comment)

    def _append(self, comment: Comment) -> None:
        if not comment.topics:
            comment.topics = self._parent_of(comment).topics
        super()._append(comment)

    def _parent_of(self, comment: Comment) -> Statement:
        """The statement `comment` replies to, from this collection or the starters."""
        if comment.reply_to is None:
            raise ValueError(f"{comment!r} has no topics and is not a reply")
        parent = self.get(comment.reply_to)
        if parent is None and self._starters is not None:
            parent = self._starters.get(comment.reply_to)
        if parent is None:
            raise ValueError(f"{comment!r} replies to unknown statement ID {comment.reply_to}")
        return parent

    # --- Threads -----------------------------------------------------------

    @property
    def top_level(self) -> "Comments":
        """Comments that are not replies (added via 'I want to say something else')."""
        return self._new(c for c in self._items if c.reply_to is None)

    @property
    def replies(self) -> "Comments":
        """Comments that reply to some other statement."""
        return self._new(c for c in self._items if c.reply_to is not None)

    @property
    def flagged(self) -> "Comments":
        return self._new(c for c in self._items if c.flag is not None)

    @property
    def unflagged(self) -> "Comments":
        return self._new(c for c in self._items if c.flag is None)

    def replies_to(self, ID: int) -> "Comments":
        """Direct replies to the statement (starter or comment) with the given ID."""
        return self._new(c for c in self._items if c.reply_to == ID)

    def by_reply_to(self) -> dict[int | None, "Comments"]:
        """Group comments by the ID they reply to (None for top-level comments)."""
        groups: dict[int | None, list[Comment]] = {}
        for c in self._items:
            groups.setdefault(c.reply_to, []).append(c)
        return {k: self._new(lst) for k, lst in groups.items()}


#################################################
##### RANDOM COMMENTS (no CSV exists for these)
#################################################

_RANDOM_COMMENT_TEMPLATES: dict[str, tuple[str, ...]] = {
    "en": (
        "I completely agree with this.",
        "I'm not sure about that, it depends on the context.",
        "This reminds me of a discussion I had with a friend.",
        "Interesting point, but I see it differently.",
        "Why do you think that?",
        "That's exactly what I've been thinking lately.",
        "I disagree, but I understand where this comes from.",
        "Could you give an example?",
    ),
    "nl": (
        "Daar ben ik het helemaal mee eens.",
        "Dat weet ik niet zo zeker, het hangt van de context af.",
        "Dit doet me denken aan een gesprek met een vriend.",
        "Interessant punt, maar ik zie het anders.",
        "Waarom denk je dat?",
        "Dat is precies wat ik de laatste tijd ook denk.",
        "Ik ben het er niet mee eens, maar ik snap waar het vandaan komt.",
        "Kun je een voorbeeld geven?",
    ),
    "fr": (
        "Je suis tout à fait d'accord.",
        "Je n'en suis pas sûr, ça dépend du contexte.",
        "Ça me rappelle une discussion avec un ami.",
        "Point intéressant, mais je vois ça autrement.",
        "Pourquoi penses-tu cela ?",
        "C'est exactement ce que je pense ces derniers temps.",
        "Je ne suis pas d'accord, mais je comprends d'où ça vient.",
        "Peux-tu donner un exemple ?",
    ),
}


def make_random_comments(
    starters: Sequence[ConversationStarter],
    n: int = 30,
    rng: random.Random | None = None,
    reply_chance: float = 0.3,
) -> list[Comment]:
    """Create `n` synthetic Comments, each replying to a random starter from `starters`.

    Each comment inherits language and topics from the statement it replies to and gets a
    timestamp shortly after it. With probability `reply_chance` a comment replies to an
    earlier generated comment instead of a starter, so the result contains small threads.
    """
    if not starters:
        raise ValueError("need at least one ConversationStarter to reply to")
    rng = rng or random.Random()
    comments: list[Comment] = []
    for _ in range(n):
        parent: Statement = (
            rng.choice(comments) if comments and rng.random() < reply_chance
            else rng.choice(starters)
        )
        templates = _RANDOM_COMMENT_TEMPLATES.get(parent.language, _RANDOM_COMMENT_TEMPLATES["en"])
        comments.append(Comment(
            text=rng.choice(templates),
            reply_to=parent.ID,
            topics=parent.topics,
            language=parent.language,
            timestamp=parent.timestamp + timedelta(minutes=rng.randint(1, 3 * 24 * 60)),
        ))
    return comments


# def seed_DB() -> dict[int, Statement]:
#     starter = ConversationStarter(text="i think", topics=("what_is_democracy",))
#     init_comments = [
#         starter,
#         Comment(text="hi", reply_to=starter.ID, topics=starter.topics),
#         Comment(text="bye", reply_to=None, topics=("protest",)),
#     ]
#     return {c.ID: c for c in init_comments}
