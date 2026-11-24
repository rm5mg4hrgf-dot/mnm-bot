"""Question bank, answer checking, scheduling and problem generation.

No Discord imports: everything here is pure and unit-tested.
"""
from __future__ import annotations

import math
import random
import re
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from typing import Any, Optional

DISCIPLINES = ("neuro", "math", "med")
DISCIPLINE_NAMES = {"neuro": "Neuroscience", "math": "Mathematics", "med": "Medicine"}
POINTS = {"easy": 5, "medium": 10, "hard": 15}
CHAMPIONSHIP_MULTIPLIER = 2
CHAMPIONSHIP_PER_DISCIPLINE = 2
TRIAL_PROBLEMS = 3
TRIAL_TIME_LIMIT_MINUTES = 20
TRIAL_GRACE_SECONDS = 30
TRIAL_POINTS_PER_PROBLEM = 15


@dataclass(frozen=True)
class Question:
    id: str
    discipline: str
    difficulty: str
    text: str
    explanation: str
    options: Optional[tuple[str, ...]] = None
    answer_index: Optional[int] = None
    answers: tuple[str, ...] = ()
    diagram: Optional[dict[str, Any]] = None

    @property
    def is_mcq(self) -> bool:
        return self.options is not None

    @property
    def points(self) -> int:
        return POINTS.get(self.difficulty, 5)

    def correct_display(self) -> str:
        if self.is_mcq:
            letter = "ABCD"[self.answer_index or 0]
            return f"{letter}. {self.options[self.answer_index or 0]}"  # type: ignore[index]
        return self.answers[0]


def question_from_dict(raw: dict[str, Any], discipline: str) -> Question:
    options = tuple(raw["options"]) if "options" in raw else None
    q = Question(
        id=raw["id"], discipline=discipline, difficulty=raw["difficulty"], text=raw["q"],
        explanation=raw["explanation"], options=options,
        answer_index=raw.get("answer") if options else None,
        answers=tuple(raw.get("answers", ())), diagram=raw.get("diagram"),
    )
    if q.difficulty not in POINTS:
        raise ValueError(f"{q.id}: bad difficulty {q.difficulty!r}")
    if q.is_mcq:
        if not (2 <= len(q.options) <= 4):  # type: ignore[arg-type]
            raise ValueError(f"{q.id}: MCQ needs 2-4 options")
        if q.answer_index is None or not (0 <= q.answer_index < len(q.options)):  # type: ignore[arg-type]
            raise ValueError(f"{q.id}: answer index out of range")
    elif not q.answers:
        raise ValueError(f"{q.id}: short-answer question needs answers")
    return q


# ----------------------------------------------------------------- answers
_NUMBER = re.compile(r"^[+-]?\d{1,15}(\.\d{1,12})?$|^[+-]?\d{1,15}/[+-]?\d{1,15}$")


def parse_number(text: str) -> Optional[Fraction]:
    """Parse '12', '-3.5', '99/100' into a Fraction. Rejects exponents and giant inputs."""
    t = text.strip().replace(",", "").replace(" ", "").rstrip(".")
    t = t.replace("−", "-")
    if not t or len(t) > 40 or not _NUMBER.match(t):
        return None
    try:
        return Fraction(t)
    except (ZeroDivisionError, ValueError):
        return None


def normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".")


def check_answer(question: Question, response: Any) -> bool:
    """MCQ: ``response`` is an option index. Short answer: free text."""
    if question.is_mcq:
        return isinstance(response, int) and response == question.answer_index
    given = str(response)
    given_num = parse_number(given)
    for accepted in question.answers:
        if given_num is not None:
            acc_num = parse_number(accepted)
            if acc_num is not None and acc_num == given_num:
                return True
        if normalise_text(given) == normalise_text(accepted):
            return True
    return False


# --------------------------------------------------------------- scheduling
def week_id(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def week_start_iso(d: date) -> str:
    monday = d - timedelta(days=d.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).isoformat(timespec="seconds")


def next_utc_midnight(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


class QuestionBank:
    """Holds all questions by discipline plus geometry and olympiad pools."""

    def __init__(self, questions_raw: dict[str, list[dict]], math_raw: dict[str, Any]):
        self.by_discipline: dict[str, list[Question]] = {
            d: sorted((question_from_dict(r, d) for r in questions_raw[d]), key=lambda q: q.id)
            for d in DISCIPLINES
        }
        self.geometry = sorted((question_from_dict(r, "math") for r in math_raw["geometry"]), key=lambda q: q.id)
        self.olympiad = sorted((question_from_dict(r, "math") for r in math_raw["olympiad"]), key=lambda q: q.id)
        ids = [q.id for pool in self.by_discipline.values() for q in pool] + \
              [q.id for q in self.geometry] + [q.id for q in self.olympiad]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate question ids in content files")

    def pool(self, discipline: str, difficulty: Optional[str] = None) -> list[Question]:
        pool = self.by_discipline[discipline]
        return [q for q in pool if difficulty in (None, "mixed") or q.difficulty == difficulty]

    def get(self, question_id: str) -> Optional[Question]:
        for pool in (*self.by_discipline.values(), self.geometry, self.olympiad):
            for q in pool:
                if q.id == question_id:
                    return q
        return None

    # -- daily
    def daily(self, d: date) -> Question:
        ordinal = d.toordinal()
        discipline = DISCIPLINES[ordinal % len(DISCIPLINES)]
        pool = self.by_discipline[discipline]
        return pool[(ordinal // len(DISCIPLINES)) % len(pool)]

    # -- championship
    def championship(self, wid: str) -> list[Question]:
        rng = random.Random(f"champ:{wid}")
        picks = {d: rng.sample(self.by_discipline[d], CHAMPIONSHIP_PER_DISCIPLINE) for d in DISCIPLINES}
        ordered: list[Question] = []
        for i in range(CHAMPIONSHIP_PER_DISCIPLINE):
            for d in DISCIPLINES:
                ordered.append(picks[d][i])
        return ordered

    # -- olympiad trials
    def trial(self, trial_id: str) -> list[Question]:
        rng = random.Random(f"trial:{trial_id}")
        return rng.sample(self.olympiad, TRIAL_PROBLEMS)


def trial_id_for(d: date) -> str:
    return f"T{week_id(d)}"


def championship_points(q: Question) -> int:
    return q.points * CHAMPIONSHIP_MULTIPLIER


# ------------------------------------------------- generated practice problems
TOPICS = ("algebra", "geometry", "number theory", "combinatorics")
_WORDS = ["BANANA", "LEVEL", "MISSISSIPPI", "STATISTICS", "ANAGRAM", "COMMITTEE"]
_TRIPLES = [(3, 4, 5), (5, 12, 13), (8, 15, 17), (7, 24, 25), (20, 21, 29)]
_HERON = [(13, 14, 15), (10, 17, 21), (9, 10, 17), (6, 25, 29)]


def _multiset_permutations(word: str) -> int:
    result = math.factorial(len(word))
    for ch in set(word):
        result //= math.factorial(word.count(ch))
    return result


def _divisor_count(n: int) -> int:
    count, p = 1, 2
    while p * p <= n:
        e = 0
        while n % p == 0:
            n //= p
            e += 1
        count *= e + 1
        p += 1
    if n > 1:
        count *= 2
    return count


def generate_problem(topic: str, rng: Optional[random.Random] = None) -> Question:
    """Generate a practice problem whose answer is computed by Python.

    Generated problems are practice only: they never award points.
    """
    rng = rng or random.Random()
    if topic == "any":
        topic = rng.choice(TOPICS)
    if topic == "algebra":
        r1, r2 = rng.randint(-9, 9), rng.randint(-9, 9)
        s, p = r1 + r2, r1 * r2
        b = f"- {s}x" if s > 0 else (f"+ {-s}x" if s < 0 else "")
        c = f"+ {p}" if p > 0 else (f"- {-p}" if p < 0 else "")
        text = f"The quadratic x^2 {b} {c} = 0 has integer roots. What is the larger root?".replace("  ", " ")
        ans, expl, diff = max(r1, r2), f"The roots are {r1} and {r2} (sum {s}, product {p}).", "medium"
    elif topic == "number theory":
        kind = rng.choice(["modexp", "divisors", "gcd"])
        if kind == "modexp":
            a, e, m = rng.randint(2, 12), rng.randint(20, 200), rng.choice([7, 9, 11, 13, 17])
            text = f"What is the remainder when {a}^{e} is divided by {m}?"
            ans, expl, diff = pow(a, e, m), "Use modular arithmetic: reduce powers using the cycle of residues.", "medium"
        elif kind == "divisors":
            n = rng.choice([72, 120, 144, 180, 210, 360, 420, 840, 900, 1001])
            text = f"How many positive divisors does {n} have?"
            ans, expl, diff = _divisor_count(n), "Factorise n into primes and multiply (exponent + 1) over all primes.", "easy"
        else:
            a, b = rng.randint(20, 300), rng.randint(20, 300)
            text = f"What is the greatest common divisor of {a} and {b}?"
            ans, expl, diff = math.gcd(a, b), "Apply the Euclidean algorithm.", "easy"
    elif topic == "combinatorics":
        if rng.random() < 0.5:
            n = rng.randint(7, 14)
            k = rng.randint(2, 4)
            text = f"In how many ways can a committee of {k} be chosen from {n} people?"
            ans, expl, diff = math.comb(n, k), f"C({n},{k}) = {n}! / ({k}! x {n - k}!).", "easy"
        else:
            w = rng.choice(_WORDS)
            text = f"How many distinct arrangements are there of the letters of the word {w}?"
            ans, expl, diff = _multiset_permutations(w), "Divide n! by the factorial of each repeated letter count.", "medium"
    elif topic == "geometry":
        if rng.random() < 0.5:
            a, b, c = rng.choice(_TRIPLES)
            text = f"A right triangle has legs {a} and {b}. What is its hypotenuse?"
            ans, expl, diff = c, f"sqrt({a}^2 + {b}^2) = {c}.", "easy"
        else:
            a, b, c = rng.choice(_HERON)
            s = (a + b + c) / 2
            area = round(math.sqrt(s * (s - a) * (s - b) * (s - c)))
            text = f"A triangle has sides {a}, {b} and {c}. What is its area?"
            ans, expl, diff = area, "Use Heron's formula with s = (a+b+c)/2.", "medium"
    else:
        raise ValueError(f"Unknown topic {topic!r}")
    qid = f"gen:{topic.replace(' ', '-')}:{zlib.crc32(text.encode()) % 10**6}"
    return Question(id=qid, discipline="math", difficulty=diff, text=text, explanation=expl,
                    answers=(str(ans),))
