# Comment2Conversation backend

## Run

From this directory:

```
python -m app.main
```

or `uvicorn app.main:app --reload`. Then open `tests/test_client.html` in a browser
and click Connect.

## Admin page

`/admin` is a spreadsheet-like page for adding, editing and deleting rows of
`data/conversation_starters.csv`, `data/comments.csv` and `data/flags.csv` (e.g. to verify
visitors' comments and review their flags). `/admin/verify` is a simpler page that only lists
unverified comments, to correct and verify them; `/admin` asks which of the two you want.
Both ask for the password set as `PASSWORD` in `app/admin.py`.

Saved changes are checked with the same loader the app uses at startup, written to the CSV,
and used by the running app right away.

## Layout

```
app/
  main.py        FastAPI app, lifespan, GET /db, uvicorn entry
  admin.py       /admin: password-protected CSV editor (page: admin.html)
  data.py        in-memory DB and seed data
  ws/
    manager.py   connection registry, anonymous IDs, broadcast
    sender.py    broadcast loop (queue of new comments, random filler)
    handlers.py  /ws route and per-connection receive loop
  models/
    fields.py    shared Annotated field types
    statements.py  Statement, Comment, ConversationStarter, Flag
    render.py    outgoing payload models
    messages.py  {type, payload} envelopes, Incoming/OutgoingMessage, render()
  i18n/
    messages.yaml  static UI strings (en, nl, fr) and topic vocabulary
    __init__.py    loader, t(), Topic enum
docs/
  asyncapi.yaml    generated: python docs/gen_asyncapi.py
tests/
  test_client.html manual websocket test page
```

## Notes

Hardcoded in the front-end:

- fade time (but the backend sends the next comment, so that sets the pace?)
- blur radius for CommentRender; `is_flagged` signals it to the front-end
- inverted colours for NewCommentRender and ConversationStarter; signalled by the different `type` tags

To be decided:

- should the flag symbol of a flagged comment change (e.g. be filled)? If so, does the backend
  need to communicate that explicitly or is `CommentRender.is_flagged == True` enough?
- time to replace a comment with a new one is fixed, so the fade timer is known in advance?
- does CommentRender need a language code?
