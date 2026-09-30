"""In-memory database. Replace with a real store when needed."""

import csv
import json
import random
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generic, TypeVar

import pandas as pd

from app.models import Comment, CommentCreate, ConversationStarter, Flag, Statement, Topic
from app.models.fields import reserve_ids

# <project root>/data/
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SEED_CONVERSATION_STARTERS_CSV = DATA_DIR / "seed_conversation_starters.csv"
# all comments: the seed replies and those sent by visitors (appended as they arrive)
COMMENTS_CSV = DATA_DIR / "comments.csv"
COMMENTS_COLUMNS = ("comment_ID", "timestamp", "text", "permission_processing",
                    "permission_sharing", "language_code", "reply_to", "topics", "verified",
                    "flag_id")
# flags sent by visitors, referenced from the comment CSVs' flag_id column; created on the first flag
FLAGS_CSV = DATA_DIR / "flags.csv"
FLAGS_COLUMNS = ("flag_id", "timestamp", "comment_ID", "reason", "donotshow")

_AFFIRMATIVE = {"1", "ja", "yes", "oui"}


def read_seed_csv(path: Path) -> pd.DataFrame:
    """Read a seed (or comments) CSV into a DataFrame.

    Shared columns: timestamp (ISO 8601, parsed to datetime), text, permission_processing,
    permission_sharing, language_code.
    The starters CSV adds conversation_starter_ID and topics (a JSON-encoded list of topic IDs, parsed to a tuple
    of Topic); the comments CSV adds comment_ID, reply_to (the ID of the statement replied
    to), topics (empty: inherited from the statement replied to), verified (1/0, parsed to
    bool) and flag_id (a flag_id in the flags CSV, empty if the comment isn't flagged).
    """
    df = pd.read_csv(path, encoding="utf-8", dtype={"permission_processing": str, "permission_sharing": str})
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
    for col in ("text", "permission_processing", "permission_sharing", "language_code"):
        if col in df:
            df[col] = df[col].fillna("").astype(str).str.strip()
    df["language_code"] = df["language_code"].str.lower()
    if "topics" in df:
        df["topics"] = df["topics"].apply(_parse_topics)
    if "verified" in df:
        df["verified"] = df["verified"].fillna(0).astype(int).astype(bool)
    if "flag_id" in df:
        df["flag_id"] = df["flag_id"].astype("Int64")
    df = df[df["text"] != ""]
    return df


def _parse_topics(value) -> tuple[Topic, ...]:
    """Parse a JSON-encoded list of topic IDs; empty/missing cells yield an empty tuple."""
    if pd.isna(value) or not str(value).strip():
        return ()
    return tuple(Topic(name) for name in json.loads(value))


def _with_permission(df: pd.DataFrame) -> pd.DataFrame:
    """Keep rows with an affirmative answer ("1"/"Ja"/"Yes") in both permission columns."""
    return df[
        df["permission_processing"].str.lower().isin(_AFFIRMATIVE)
        & df["permission_sharing"].str.lower().isin(_AFFIRMATIVE)
    ]


def _without_refusal(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with a non-affirmative answer in either permission column; rows left
    empty (comments sent through the app, which asks for no permissions) are kept."""
    def ok(col: str) -> pd.Series:
        return (df[col] == "") | df[col].str.lower().isin(_AFFIRMATIVE)
    return df[ok("permission_processing") & ok("permission_sharing")]


def load_flags(path: Path = FLAGS_CSV) -> dict[int, Flag]:
    """Read the flags CSV into {flag_id: Flag}."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {
            int(row["flag_id"]): Flag(
                flag_id=int(row["flag_id"]),
                timestamp=datetime.fromisoformat(row["timestamp"]),
                comment_ID=int(row["comment_ID"]),
                reason=row["reason"],
                donotshow=row["donotshow"].strip().lower() in {"1", "true"},
            )
            for row in csv.DictReader(f)
        }


def _flag_of(row, flags: dict[int, Flag]) -> Flag | None:
    """The Flag referenced by a comment row's flag_id (None if the row has none).

    The comment row's flag_id decides which comment is flagged. A flag_id missing from the
    flags CSV still flags the comment, with an empty reason.
    """
    if pd.isna(row.flag_id):
        return None
    flag = flags.get(int(row.flag_id))
    if flag is None:
        return Flag(flag_id=int(row.flag_id), reason="", donotshow=False,
                    comment_ID=int(row.comment_ID))
    return flag


def load_conversation_starters(
    path: Path = SEED_CONVERSATION_STARTERS_CSV,
    require_permission: bool = True,
) -> list[ConversationStarter]:
    """Build ConversationStarters from the seed starters CSV.

    The CSV has one row per language of each starter, sharing a conversation_starter_ID,
    which becomes the starter's ID. The group's first row is the starter itself (its text,
    language, timestamp and topics); the group's other rows become its `translations`.
    With `require_permission`, rows lacking an affirmative answer in both permission
    columns are skipped.
    """
    df = read_seed_csv(path)
    if require_permission:
        df = _with_permission(df)
    starters = []
    for seed_ID, group in df.groupby("conversation_starter_ID", sort=False):
        first = group.iloc[0]
        starters.append(ConversationStarter(
            ID=int(seed_ID),
            text=first.text,
            language=first.language_code,
            timestamp=first.timestamp.to_pydatetime(),
            topics=first.topics,
            translations={
                row.language_code: row.text
                for row in group.iloc[1:].itertuples(index=False)
            },
        ))
    return starters


def load_comments_csv(
    path: Path = COMMENTS_CSV,
    flags: dict[int, Flag] | None = None,
    require_permission: bool = True,
) -> list[Comment]:
    """Build Comments from the comments CSV (appended to by `append_comment_csv`).

    Comments with empty topics inherit them from the statement they reply to (see
    `Comments`). Flags are looked up in `flags` (see `load_flags`). With
    `require_permission`, rows with a non-affirmative answer in a permission column are
    skipped; empty permission cells don't count as a refusal.
    """
    if not path.exists():
        return []
    df = read_seed_csv(path)
    if require_permission:
        df = _without_refusal(df)
    return [
        Comment(
            ID=int(row.comment_ID),
            text=row.text,
            language=row.language_code,
            timestamp=row.timestamp.to_pydatetime(),
            reply_to=None if pd.isna(row.reply_to) else int(row.reply_to),
            topics=row.topics,
            verified=row.verified,
            flag=_flag_of(row, flags or {}),
        )
        for row in df.itertuples(index=False)
    ]


def append_comment_csv(comment: Comment, path: Path = COMMENTS_CSV) -> None:
    """Append a new comment to the comments CSV, writing the header for a new file."""
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(COMMENTS_COLUMNS)
        writer.writerow([
            comment.ID,
            comment.timestamp.isoformat(timespec="seconds"),
            comment.text,
            "",  # permission_processing: not asked for in the app
            "",  # permission_sharing
            comment.language,
            "" if comment.reply_to is None else comment.reply_to,
            json.dumps([t.value for t in comment.topics]),
            int(comment.verified),
            "" if comment.flag is None or comment.flag.flag_id is None else comment.flag.flag_id,
        ])


def store_flag_csv(
    flag: Flag,
    flags_path: Path = FLAGS_CSV,
    comment_paths: Iterable[Path] = (COMMENTS_CSV,),
) -> bool:
    """Store `flag` in the flags CSV and reference it from its comment's row.

    The flag gets the next free flag_id (also set on `flag`) and is appended to the flags
    CSV; the row with the flag's comment_ID, in the first of `comment_paths` that has it,
    gets that flag_id (rewriting that file). Returns False if no file has the comment.
    """
    flag.flag_id = max(load_flags(flags_path), default=0) + 1
    is_new = not flags_path.exists()
    with flags_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(FLAGS_COLUMNS)
        writer.writerow([flag.flag_id, flag.timestamp.isoformat(timespec="seconds"),
                         flag.comment_ID, flag.reason, int(flag.donotshow)])

    for path in comment_paths:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        id_col, flag_col = header.index("comment_ID"), header.index("flag_id")
        for row in rows[1:]:
            if row[id_col] == str(flag.comment_ID):
                row[flag_col] = str(flag.flag_id)
                with path.open("w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerows(rows)
                return True
    return False


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
    """Static (read-only) collection of ConversationStarters, normally loaded from the seed CSV."""






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
    def sampleable(self) -> "Comments":
        """Comments that may be sampled into conversations: verified and not flagged."""
        return self._new(c for c in self._items if c.verified and c.flag is None)

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


def load_seed(
    starters_path: Path = SEED_CONVERSATION_STARTERS_CSV,
    comments_path: Path = COMMENTS_CSV,
    flags_path: Path = FLAGS_CSV,
    require_permission: bool = True,
) -> tuple[ConversationStarters, Comments]:
    """Load the seed conversation starters and the comments, with the comments' flags.

    IDs come from the CSVs; IDs generated afterwards (for new comments) continue after the
    highest of them.
    """
    starters = ConversationStarters(load_conversation_starters(starters_path, require_permission))
    flags = load_flags(flags_path)
    comments = Comments(load_comments_csv(comments_path, flags, require_permission),
                        starters=starters)
    overlap = set(starters.ids) & set(comments.ids)
    if overlap:
        raise ValueError(f"IDs used by both a starter and a comment: {sorted(overlap)}")
    reserve_ids(max((*starters.ids, *comments.ids), default=0))
    return starters, comments


#################################################
##### RANDOM COMMENTS (no CSV exists for these)
#################################################

# _RANDOM_COMMENT_TEMPLATES: dict[str, tuple[str, ...]] = {
#     "en": (
#         "I completely agree with this.",
#         "I'm not sure about that, it depends on the context.",
#         "This reminds me of a discussion I had with a friend.",
#         "Interesting point, but I see it differently.",
#         "Why do you think that?",
#         "That's exactly what I've been thinking lately.",
#         "I disagree, but I understand where this comes from.",
#         "Could you give an example?",
#     ),
#     "nl": (
#         "Daar ben ik het helemaal mee eens.",
#         "Dat weet ik niet zo zeker, het hangt van de context af.",
#         "Dit doet me denken aan een gesprek met een vriend.",
#         "Interessant punt, maar ik zie het anders.",
#         "Waarom denk je dat?",
#         "Dat is precies wat ik de laatste tijd ook denk.",
#         "Ik ben het er niet mee eens, maar ik snap waar het vandaan komt.",
#         "Kun je een voorbeeld geven?",
#     ),
#     "fr": (
#         "Je suis tout à fait d'accord.",
#         "Je n'en suis pas sûr, ça dépend du contexte.",
#         "Ça me rappelle une discussion avec un ami.",
#         "Point intéressant, mais je vois ça autrement.",
#         "Pourquoi penses-tu cela ?",
#         "C'est exactement ce que je pense ces derniers temps.",
#         "Je ne suis pas d'accord, mais je comprends d'où ça vient.",
#         "Peux-tu donner un exemple ?",
#     ),
# }


# def make_random_comments(
#     starters: Sequence[ConversationStarter],
#     n: int = 30,
#     rng: random.Random | None = None,
#     reply_chance: float = 0.3,
# ) -> list[Comment]:
#     """Create `n` synthetic Comments, each replying to a random starter from `starters`.

#     Each comment inherits language and topics from the statement it replies to and gets a
#     timestamp shortly after it. With probability `reply_chance` a comment replies to an
#     earlier generated comment instead of a starter, so the result contains small threads.
#     """
#     if not starters:
#         raise ValueError("need at least one ConversationStarter to reply to")
#     rng = rng or random.Random()
#     comments: list[Comment] = []
#     for _ in range(n):
#         parent: Statement = (
#             rng.choice(comments) if comments and rng.random() < reply_chance
#             else rng.choice(starters)
#         )
#         templates = _RANDOM_COMMENT_TEMPLATES.get(parent.language, _RANDOM_COMMENT_TEMPLATES["en"])
#         comments.append(Comment(
#             text=rng.choice(templates),
#             reply_to=parent.ID,
#             topics=parent.topics,
#             language=parent.language,
#             timestamp=parent.timestamp + timedelta(minutes=rng.randint(1, 3 * 24 * 60)),
#         ))
#     return comments
