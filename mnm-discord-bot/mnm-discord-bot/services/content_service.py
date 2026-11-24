"""Loads and validates the static educational content shipped in data/initial_content/."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.challenge_service import QuestionBank, question_from_dict, Question

CONTENT_DIR = Path(__file__).resolve().parent.parent / "data" / "initial_content"


class ContentError(Exception):
    pass


def _load(name: str) -> dict[str, Any]:
    path = CONTENT_DIR / name
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentError(f"Could not load content file {name}: {exc}") from exc


def validate_items(items: list[dict], label: str) -> None:
    """Browser items must fit comfortably inside a Discord embed."""
    if not (1 <= len(items) <= 25):
        raise ContentError(f"{label}: needs 1-25 items for a select menu")
    for it in items:
        if not it.get("name") or not it.get("summary"):
            raise ContentError(f"{label}: item missing name or summary")
        if len(it["name"]) > 90:
            raise ContentError(f"{label}: name too long: {it['name']}")
        if len(it["summary"]) > 600:
            raise ContentError(f"{label}: summary too long: {it['name']}")
        for field in it.get("fields", []):
            if len(field) != 2 or len(field[0]) > 250 or len(field[1]) > 1000:
                raise ContentError(f"{label}: bad field in {it['name']}")


@dataclass
class Content:
    regions: list[dict]
    neural_topics: list[dict]
    memory_topics: list[dict]
    systems: list[dict]
    pathology: list[dict]
    molecular: list[dict]
    frontiers: list[dict]
    infinity_vault: list[dict]
    chronicles: list[Question]
    bank: QuestionBank


@lru_cache(maxsize=1)
def load_content() -> Content:
    neuro = _load("neuroscience.json")
    med = _load("medicine.json")
    maths = _load("mathematics.json")
    questions = _load("questions.json")
    content = Content(
        regions=neuro["regions"], neural_topics=neuro["neural_topics"], memory_topics=neuro["memory_topics"],
        systems=med["systems"], pathology=med["pathology"], molecular=med["molecular"],
        frontiers=med["frontiers"], infinity_vault=maths["infinity_vault"],
        chronicles=[question_from_dict(r, "med") for r in med["chronicles"]],
        bank=QuestionBank(questions, maths),
    )
    for label in ("regions", "neural_topics", "memory_topics", "systems", "pathology",
                  "molecular", "frontiers", "infinity_vault"):
        validate_items(getattr(content, label), label)
    return content
