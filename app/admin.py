"""/admin: a password-protected page for editing the conversation starters, comments and flags CSVs.

The password is PASSWORD below. This is low security by design: it only keeps public
visitors out. No cookies are used: logging in returns a token, which the pages keep in
localStorage and send in the X-Admin-Token header.

The page (admin.html) sends a list of row changes (add/update/delete); they are applied to
the CSV as it is on disk at that moment, so comments sent by visitors in the meantime are
kept. The result is validated with the app's own loader (`load_seed`) before it is written,
so a bad edit can't stop the app from starting, and the running app reloads the new data.
/admin/verify (admin_verify.html) is a simpler page on the same API for verifying comments.

The handlers are `async def` without any `await` between reading and writing a CSV, so they
never interleave with the websocket handlers that append comments and flags.
"""

import csv
import hashlib
import hmac
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app import data
from app.i18n import TOPICS
from app.models.fields import SUPPORTED_LANGUAGES

PASSWORD = "C2C-admin"
TOKEN_HEADER = "X-Admin-Token"
PAGE = Path(__file__).with_name("admin.html")
VERIFY_PAGE = Path(__file__).with_name("admin_verify.html")  # just for verifying comments

router = APIRouter(prefix="/admin", include_in_schema=False)


# --- Tables ----------------------------------------------------------------------------

# per column: (label shown on the page, kind, help text shown when hovering the label)
# kinds: id, optional_id, timestamp, text, optional_text, language, topics, yesno,
# hidden (not shown on the page, which sends the stored value back; new rows get '1'),
# flag (a comment's flag_id: shown as a link to the flag; set via /api/flags, can be cleared)
STARTER_COLUMNS = {
    "conversation_starter_ID": ("Starter ID", "id",
        "Rows with the same ID are one starter in different languages. "
        "The first of them is the original, the others are its translations."),
    "language_code": ("Language", "language", "The language of this row's text."),
    "text": ("Text", "text", "The conversation starter as shown in the app."),
    "topics": ("Topics", "topics", "The topics this starter is about (at least one)."),
    "permission_processing": ("Permission: processing", "hidden", ""),
    "permission_sharing": ("Permission: sharing", "hidden", ""),
    "timestamp": ("Date & time", "timestamp", "When it was written, e.g. 2026-09-29T14:30:00."),
}
COMMENT_COLUMNS = {
    "comment_ID": ("Comment ID", "id", "Unique number of this comment."),
    "verified": ("Verified", "yesno",
        "Only verified comments are shown in the app. New comments from visitors start "
        "unverified."),
    "text": ("Text", "text", "The comment as shown in the app."),
    "reply_to": ("Reply to (ID)", "optional_id",
        "The ID of the starter or comment this replies to. Empty if it isn't a reply."),
    "language_code": ("Language", "language", "The language of the comment."),
    "topics": ("Topics", "topics",
        "Leave empty to use the topics of the starter or comment it replies to."),
    "flag_id": ("Flag", "flag",
        "Set when a visitor flagged the comment; flagged comments are not shown in the app. "
        "Click an empty cell to flag the comment; remove the link to unflag it."),
    "permission_processing": ("Permission: processing", "hidden", ""),
    "permission_sharing": ("Permission: sharing", "hidden", ""),
    "timestamp": ("Date & time", "timestamp", "When it was written, e.g. 2026-09-29T14:30:00."),
}
FLAG_COLUMNS = {
    "flag_id": ("Flag ID", "id", "Unique number of this flag; a comment's Flag ID points here."),
    "comment_ID": ("Comment ID", "id", "The comment that was flagged."),
    "reason": ("Reason", "optional_text", "The reason the visitor gave."),
    "donotshow": ("Do not show", "yesno", "Sent by the app along with the flag."),
    "timestamp": ("Date & time", "timestamp", "When the comment was flagged."),
}
TABLES = {
    "starters": {"title": "Conversation starters", "columns": STARTER_COLUMNS,
                 "key": ("conversation_starter_ID", "language_code")},
    "comments": {"title": "Comments", "columns": COMMENT_COLUMNS, "key": ("comment_ID",)},
    # a comment is flagged by the flag_id in its row; deleting the flag here also clears that
    "flags": {"title": "Flags", "columns": FLAG_COLUMNS, "key": ("flag_id",)},
}


def _path(name: str) -> Path:
    return {"starters": data.CONVERSATION_STARTERS_CSV, "comments": data.COMMENTS_CSV,
            "flags": data.FLAGS_CSV}[name]


def _table(name: str) -> dict:
    if name not in TABLES:
        raise HTTPException(404, f"unknown table {name!r}")
    return TABLES[name]


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """The CSV's header and its rows, as strings exactly as stored."""
    if path == data.FLAGS_CSV and not path.exists():  # created with the first flag
        return list(data.FLAGS_COLUMNS), []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def _write(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    """Write the CSV via a temporary file, so a crash never leaves it half written."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".csv.tmp")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def _key(name: str, row: dict[str, str]) -> list[str]:
    return [row.get(col, "") for col in TABLES[name]["key"]]


def _topics_to_cell(value: str) -> str:
    """'["a", "b"]' (as stored) -> 'a, b' (as shown on the page)."""
    try:
        return ", ".join(json.loads(value)) if value.strip() else ""
    except (json.JSONDecodeError, TypeError):
        return value


def _for_page(name: str, row: dict[str, str]) -> dict[str, str]:
    shown = dict(row)
    if "topics" in row:
        shown["topics"] = _topics_to_cell(row["topics"])
    return {"key": _key(name, row), "values": shown}


# --- Validation ------------------------------------------------------------------------

class AdminError(ValueError):
    """A problem with the submitted data, explained for the person editing it."""


def _clean(name: str, row: dict, header: list[str]) -> dict[str, str]:
    """Check one submitted row and turn it into the strings stored in the CSV."""
    columns = TABLES[name]["columns"]
    out = {}
    for col in header:
        label, kind, _ = columns.get(col, (col, "text", ""))
        value = str(row.get(col) if row.get(col) is not None else "").strip()
        if kind == "id":
            if not value.isdigit() or int(value) == 0:
                raise AdminError(f"'{label}' must be a whole number above 0 (got '{value}').")
            value = str(int(value))
        elif kind == "optional_id":
            if value and not value.isdigit():
                raise AdminError(f"'{label}' must be a whole number or empty (got '{value}').")
            value = str(int(value)) if value else ""
        elif kind == "timestamp":
            try:
                ts = datetime.fromisoformat(value) if value else datetime.now()
            except ValueError:
                raise AdminError(f"'{label}' must look like 2026-09-29T14:30:00 (got '{value}').")
            value = ts.isoformat(timespec="seconds")
        elif kind == "text":
            if not value:
                raise AdminError(f"'{label}' can't be empty.")
        elif kind == "optional_text":
            pass
        elif kind == "language":
            value = value.lower()
            if value not in SUPPORTED_LANGUAGES:
                raise AdminError(f"'{label}' must be one of {', '.join(SUPPORTED_LANGUAGES)} "
                                 f"(got '{value}').")
        elif kind == "topics":
            topics = [t.strip() for t in value.split(",") if t.strip()]
            unknown = [t for t in topics if t not in TOPICS]
            if unknown:
                raise AdminError(f"Unknown topic(s): {', '.join(unknown)}.")
            if name == "starters" and not topics:
                raise AdminError("A conversation starter needs at least one topic.")
            value = json.dumps(topics) if topics else ""
        elif kind == "yesno":
            if value.lower() not in {"1", "0", "yes", "no", "true", "false", ""}:
                raise AdminError(f"'{label}' must be Yes or No (got '{value}').")
            value = "1" if value.lower() in {"1", "yes", "true"} else "0"
        out[col] = value
    return out


def _check_loadable(csvs: dict[str, tuple[list[str], list[dict]]]):
    """Raise AdminError if the app couldn't start with these CSVs ({table name: (header, rows)})."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = {name: Path(tmp, name + ".csv") for name in TABLES}
        for name, path in paths.items():
            _write(path, *csvs[name])
        try:
            return data.load_seed(paths["starters"], paths["comments"], paths["flags"])
        except Exception as e:  # anything load_seed raises means the app wouldn't start
            raise AdminError(f"The app could not use this data: {e}") from e


# --- Password --------------------------------------------------------------------------

def _token(password: str) -> str:
    # changes with the password, so changing it logs everyone out
    return hmac.new(password.encode(), b"c2c-admin", hashlib.sha256).hexdigest()


def _require_login(request: Request) -> None:
    if not hmac.compare_digest(request.headers.get(TOKEN_HEADER, ""), _token(PASSWORD)):
        raise HTTPException(401, "Please log in.")


# --- Routes ----------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def admin_page():
    """The admin page itself; it holds no data and asks for the password when needed."""
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@router.get("/verify", response_class=HTMLResponse)
async def verify_page():
    """A simpler page that only lists unverified comments, to verify (and correct) them."""
    return HTMLResponse(VERIFY_PAGE.read_text(encoding="utf-8"))


class Login(BaseModel):
    password: str


@router.post("/api/login")
async def login(body: Login):
    """Returns the token to send in the X-Admin-Token header (logging out is forgetting it)."""
    if not hmac.compare_digest(body.password.encode(), PASSWORD.encode()):
        raise HTTPException(401, "Wrong password.")
    return {"token": _token(PASSWORD)}


@router.get("/api/meta")
async def meta(request: Request):
    _require_login(request)
    return {
        "languages": list(SUPPORTED_LANGUAGES),
        "topics": {topic: labels.get("en") or topic for topic, labels in TOPICS.items()},
        "tables": {name: {"title": t["title"], "key": t["key"],
                          "columns": {col: {"label": label, "kind": kind, "help": help}
                                      for col, (label, kind, help) in t["columns"].items()}}
                   for name, t in TABLES.items()},
    }


@router.get("/api/tables/{name}")
async def get_table(name: str, request: Request):
    _require_login(request)
    _table(name)
    header, rows = _read(_path(name))
    return {"header": header, "rows": [_for_page(name, row) for row in rows]}


@router.get("/api/tables/{name}/download")
async def download_table(name: str, request: Request):
    _require_login(request)
    _table(name)
    path = _path(name)
    if not path.exists():
        raise HTTPException(404, "There is no file yet.")
    return FileResponse(path, media_type="text/csv", filename=path.name)


class Change(BaseModel):
    action: str  # "add", "update" or "delete"
    key: list[str] | None = None  # the row's key as sent by GET (for update/delete)
    values: dict[str, str | None] | None = None  # the row's new values (for add/update)


class Changes(BaseModel):
    changes: list[Change]


@router.post("/api/tables/{name}")
async def save_table(name: str, body: Changes, request: Request):
    """Apply the changes to the CSV on disk, validate, write, and reload the app's data."""
    _require_login(request)
    table = _table(name)
    path = _path(name)
    header, rows = _read(path)
    try:
        rows = _apply(name, header, rows, body.changes)
        keys = [tuple(_key(name, row)) for row in rows]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if duplicates:
            labels = " + ".join(table["columns"][col][0] for col in table["key"])
            raise AdminError(f"Each row needs a unique {labels}; used more than once: "
                             + "; ".join(" + ".join(k) for k in duplicates))
        csvs = {other: _read(_path(other)) for other in TABLES}
        csvs[name] = (header, rows)
        if name == "flags":  # unflag the comments whose flag was deleted
            kept = {row["flag_id"] for row in rows}
            for comment in csvs["comments"][1]:
                if comment.get("flag_id") and comment["flag_id"] not in kept:
                    comment["flag_id"] = ""
        starters, comments = _check_loadable(csvs)
    except AdminError as e:
        raise HTTPException(422, str(e))
    _write(path, header, rows)
    if name == "flags":
        _write(_path("comments"), *csvs["comments"])
    request.app.state.starters, request.app.state.comments = starters, comments
    return await get_table(name, request)


class NewFlag(BaseModel):
    comment_ID: int
    reason: str = ""


@router.post("/api/flags")
async def create_flag(body: NewFlag, request: Request):
    """Flag a comment right away, like a visitor would; returns the new flag's row."""
    _require_login(request)
    _, comments = _read(_path("comments"))
    if not any(row["comment_ID"] == str(body.comment_ID) for row in comments):
        raise HTTPException(404, f"There is no saved comment {body.comment_ID}; save it first.")
    flag = data.Flag(comment_ID=body.comment_ID, reason=body.reason.strip(), donotshow=True)
    # appends to data/flags.csv and sets the flag_id in the comment's row
    data.store_flag_csv(flag, _path("flags"), [_path("comments")])
    request.app.state.starters, request.app.state.comments = data.load_seed(
        _path("starters"), _path("comments"), _path("flags"))
    header, rows = _read(_path("flags"))
    row = next(row for row in rows if row["flag_id"] == str(flag.flag_id))
    return _for_page("flags", row)


def _flag_columns(name: str, header: list[str]) -> list[str]:
    columns = TABLES[name]["columns"]
    return [col for col in header if col in columns and columns[col][1] == "flag"]


def _apply(name: str, header: list[str], rows: list[dict[str, str]],
           changes: list[Change]) -> list[dict[str, str]]:
    rows = list(rows)
    index = {tuple(_key(name, row)): i for i, row in enumerate(rows)}
    deleted = set()
    for change in changes:
        where = ""
        if change.action in ("update", "delete"):
            i = index.get(tuple(change.key or ()))
            if i is None or i in deleted:
                raise AdminError(f"The row {' + '.join(change.key or ())} no longer exists; "
                                 "someone may have changed the data. Reload the page.")
            where = f"Row {' + '.join(change.key)}: "
        try:
            if change.action == "delete":
                deleted.add(i)
            elif change.action == "update":
                # flag columns are only sent when the page cleared them (unflagging); otherwise
                # they keep their stored value, which may be a flag sent after the page loaded
                values = change.values or {}
                flags = {col: "" if col in values and not values[col] else rows[i].get(col, "")
                         for col in _flag_columns(name, header)}
                rows[i] = {**_clean(name, values, header), **flags}
            elif change.action == "add":
                flags = {col: "" for col in _flag_columns(name, header)}
                rows.append({**_clean(name, change.values or {}, header), **flags})
            else:
                raise AdminError(f"unknown action {change.action!r}")
        except AdminError as e:
            raise AdminError(where + str(e) if where else f"New row: {e}") from e
    return [row for i, row in enumerate(rows) if i not in deleted]
