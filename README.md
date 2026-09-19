# Comment2Conversation backend

## Run

From this directory:

```
python -m app.main
```

or `uvicorn app.main:app --reload`. Then open `tests/test_client.html` in a browser
and click Connect.

## Layout

```
app/
  main.py        FastAPI app, lifespan, GET /db, uvicorn entry
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
