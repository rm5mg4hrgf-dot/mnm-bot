"""Discord-free moderation logic (anti-spam, content filter, hierarchy rules)."""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, Optional, Tuple

MAX_TIMEOUT_MINUTES = 28 * 24 * 60  # Discord limit: 28 days
AUTO_TIMEOUT_WARNINGS = 3           # auto-timeout when active warnings reach this number
AUTO_TIMEOUT_MINUTES = 60


@dataclass(frozen=True)
class SpamVerdict:
    spam: bool
    reason: str = ""


class SpamTracker:
    """In-memory sliding-window spam detection.

    Flags: too many messages in a short window, repeated identical messages and
    mass mentions. State is pruned so memory stays bounded.
    """

    def __init__(self, max_messages: int = 6, window_seconds: float = 8.0,
                 duplicate_limit: int = 3, duplicate_window: float = 30.0,
                 max_mentions: int = 5, max_tracked_users: int = 5000):
        self.max_messages = max_messages
        self.window = window_seconds
        self.duplicate_limit = duplicate_limit
        self.duplicate_window = duplicate_window
        self.max_mentions = max_mentions
        self.max_tracked_users = max_tracked_users
        self._events: Dict[int, Deque[Tuple[float, str]]] = {}

    def check(self, user_id: int, content: str, mention_count: int = 0,
              now: Optional[float] = None) -> SpamVerdict:
        now = time.monotonic() if now is None else now
        if mention_count > self.max_mentions:
            return SpamVerdict(True, f"Mass mention ({mention_count} mentions)")
        q = self._events.setdefault(user_id, deque(maxlen=20))
        q.append((now, content.strip().lower()))
        while q and now - q[0][0] > max(self.window, self.duplicate_window):
            q.popleft()
        recent = [e for e in q if now - e[0] <= self.window]
        if len(recent) > self.max_messages:
            return SpamVerdict(True, f"Message flood ({len(recent)} messages in {self.window:.0f}s)")
        text = content.strip().lower()
        if text:
            dupes = sum(1 for t, c in q if c == text and now - t <= self.duplicate_window)
            if dupes >= self.duplicate_limit:
                return SpamVerdict(True, "Repeated identical messages")
        if len(self._events) > self.max_tracked_users:
            self._prune(now)
        return SpamVerdict(False)

    def reset(self, user_id: int) -> None:
        self._events.pop(user_id, None)

    def _prune(self, now: float) -> None:
        horizon = max(self.window, self.duplicate_window)
        for uid in [u for u, q in self._events.items() if not q or now - q[-1][0] > horizon]:
            del self._events[uid]


class ContentFilter:
    """Case-insensitive whole-word filter. The word list is managed by admins."""

    def __init__(self, words: Iterable[str] = ()):
        self._pattern: Optional[re.Pattern[str]] = None
        self.set_words(words)

    def set_words(self, words: Iterable[str]) -> None:
        cleaned = sorted({w.strip().lower() for w in words if w and w.strip()}, key=len, reverse=True)
        if not cleaned:
            self._pattern = None
            return
        alternation = "|".join(re.escape(w) for w in cleaned)
        self._pattern = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)

    def find(self, text: str) -> Optional[str]:
        if not self._pattern:
            return None
        m = self._pattern.search(text)
        return m.group(0).lower() if m else None


def normalise_filter_word(word: str) -> str:
    word = word.strip().lower()
    if not (2 <= len(word) <= 40):
        raise ValueError("Filter words must be 2-40 characters long.")
    return word


def can_moderate(actor_top_position: int, target_top_position: int, *, actor_is_owner: bool,
                 target_is_owner: bool, target_is_bot: bool, same_user: bool,
                 bot_top_position: int) -> Optional[str]:
    """Return an error message if the action is not allowed, else None.

    Rules: nobody acts on themselves, the owner or bots; the actor must outrank
    the target (unless the actor is the server owner); the bot itself must also
    outrank the target.
    """
    if same_user:
        return "You cannot moderate yourself."
    if target_is_owner:
        return "The server owner cannot be moderated."
    if target_is_bot:
        return "Bots cannot be moderated with this command."
    if not actor_is_owner and actor_top_position <= target_top_position:
        return "You can only moderate members whose highest role is below yours."
    if bot_top_position <= target_top_position:
        return "My highest role must be above the target's highest role."
    return None
