"""Generate docs/asyncapi.yaml from the message models used by the app.

Run from anywhere:  python docs/gen_asyncapi.py
"""

import sys
from pathlib import Path
from typing import get_args

import yaml
from pydantic.json_schema import models_json_schema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402
from app.models import IncomingMessage, OutgoingMessage  # noqa: E402

REF = "#/components/schemas/{model}"


def union_members(annotated) -> list[type]:
    """Annotated[Union[A, B], Field(discriminator=...)] -> [A, B]"""
    union = get_args(annotated)[0]
    return list(get_args(union))


def snake(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name).lstrip("_")


incoming = union_members(IncomingMessage)   # client -> server, validated
outgoing = union_members(OutgoingMessage)   # server -> client, serialised

# One schema pass so every model lands in a single $defs and refs are consistent.
_, top = models_json_schema(
    [(m, "validation") for m in incoming] + [(m, "serialization") for m in outgoing],
    ref_template=REF,
)
schemas = top["$defs"]


def type_tag(model) -> str:
    return model.model_fields["type"].default


def message_component(model, direction: str) -> dict:
    return {
        "name": model.__name__,
        "title": model.__name__,
        "summary": f"{direction}: `type` = \"{type_tag(model)}\"",
        "contentType": "application/json",
        "payload": {"$ref": f"#/components/schemas/{model.__name__}"},
    }


messages = {m.__name__: message_component(m, "client → server") for m in incoming}
messages |= {m.__name__: message_component(m, "server → client") for m in outgoing}

channel_messages = {snake(m.__name__): {"$ref": f"#/components/messages/{m.__name__}"}
                    for m in incoming + outgoing}

spec = {
    "asyncapi": "3.0.0",
    "info": {
        "title": app.title,
        "version": app.version,
        "description": (
            "WebSocket API of the Comment2Conversation backend.\n\n"
            "A single channel `/ws` carries JSON envelopes of the form "
            "`{\"type\": <tag>, \"payload\": {...}}` in both directions. "
            "`type` discriminates the payload schema.\n\n"
            "Clients are assigned an anonymous ID per connection; it is never part of a payload. "
            "Validation failures are answered with `{\"error\": [...]}` (Pydantic error list) "
            "on the same connection.\n\n"
            "The REST endpoint `GET /db` is documented by the OpenAPI spec, not here."
        ),
    },
    "defaultContentType": "application/json",
    "servers": {
        "local": {
            "host": "127.0.0.1:8000",
            "protocol": "ws",
            "description": "Development server started with `python -m app.main`.",
        }
    },
    "channels": {
        "ws": {
            "address": "/ws",
            "description": "Bidirectional channel. One connection per client.",
            "messages": channel_messages,
        }
    },
    "operations": {
        "receiveFromClient": {
            "action": "receive",
            "channel": {"$ref": "#/channels/ws"},
            "summary": "Messages the server accepts from a client.",
            "messages": [{"$ref": f"#/channels/ws/messages/{snake(m.__name__)}"} for m in incoming],
        },
        "broadcastToClients": {
            "action": "send",
            "channel": {"$ref": "#/channels/ws"},
            "summary": "Messages the server broadcasts to every connected client.",
            "description": (
                "Every 3–7 s the server broadcasts either a freshly submitted comment "
                "(`new_comment`) or a randomly chosen statement from the database "
                "(`comment` or `conversation_starter`), alternating the `slot`."
            ),
            "messages": [{"$ref": f"#/channels/ws/messages/{snake(m.__name__)}"} for m in outgoing],
        },
    },
    "components": {
        "messages": messages,
        "schemas": schemas,
    },
}

with (ROOT / "docs" / "asyncapi.yaml").open("w", encoding="utf-8") as f:
    yaml.safe_dump(spec, f, sort_keys=False, allow_unicode=True, width=100)

print(f"asyncapi.yaml: {len(messages)} messages, {len(schemas)} schemas")
