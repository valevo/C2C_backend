"""Static user-facing strings and the topic vocabulary, loaded from messages.yaml."""

from enum import Enum
from pathlib import Path
from typing import Literal

import yaml

Language = Literal["en", "nl", "fr"]

DEFAULT_LANGUAGE: Language = "en"

_YAML_PATH = Path(__file__).with_name("messages.yaml")

with _YAML_PATH.open(encoding="utf-8") as f:
    MESSAGES: dict = yaml.safe_load(f)

# {topic_id: {lang: label}}
TOPICS: dict[str, dict[str, str]] = MESSAGES["new_comment"]["topics"]

# Language-independent topic IDs; the labels live in TOPICS.
Topic = Enum("Topic", {k: k for k in TOPICS}, type=str)


def t(key: str, lang: Language = DEFAULT_LANGUAGE):
    """Return the translation for a dotted key such as "main.flag_button".

    Falls back to the default language if `lang` has no entry or the entry is empty.
    """
    node = MESSAGES
    for part in key.split("."):
        node = node[part]
    value = node.get(lang)
    return value if value is not None else node.get(DEFAULT_LANGUAGE)


def topic_label(topic: Topic, lang: Language = DEFAULT_LANGUAGE) -> str:
    labels = TOPICS[topic.value]
    return labels.get(lang) or labels[DEFAULT_LANGUAGE]
