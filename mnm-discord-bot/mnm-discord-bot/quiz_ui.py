"""Reusable question UI: multiple choice buttons or a short-answer modal.

Used by the daily challenge, the championship, NeuroChallenge, Clinical
Chronicles, Geometry Lab and Problem Reactor. Scoring goes through
``Database.record_attempt`` so a question can never score twice.
"""
from __future__ import annotations

import io
import logging
from typing import Awaitable, Callable, Optional

import discord

from services.challenge_service import DISCIPLINE_NAMES, Question, check_answer
from ui import BaseView, CallbackButton, ERROR, SUCCESS, make_embed, present

log = logging.getLogger("mnm.quiz")

Callback = Callable[[discord.Interaction], Awaitable[None]]


def question_embed(q: Question, *, title: str, color: int, points: int, footer: Optional[str] = None,
                   result: Optional[str] = None, correct: Optional[bool] = None) -> discord.Embed:
    body = q.text
    if q.is_mcq:
        body += "\n\n" + "\n".join(f"**{'ABCD'[i]}.** {opt}" for i, opt in enumerate(q.options or ()))
    meta = f"{DISCIPLINE_NAMES.get(q.discipline, q.discipline)} · {q.difficulty.title()}"
    meta += f" · {points} pts" if points else " · practice (no points)"
    embed_color = color if correct is None else (SUCCESS if correct else ERROR)
    embed = make_embed(title, body, color=embed_color, footer=f"{meta}" + (f" · {footer}" if footer else ""))
    if result:
        embed.add_field(name="Result", value=result[:1024], inline=False)
        embed.add_field(name="Explanation", value=q.explanation[:1024], inline=False)
    return embed


class _OptionButton(discord.ui.Button):
    def __init__(self, index: int, view: "QuestionView"):
        super().__init__(label="ABCD"[index], style=discord.ButtonStyle.secondary, row=0)
        self.index, self.qview = index, view

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self.qview.resolve(interaction, self.index)


class _AnswerModal(discord.ui.Modal):
    def __init__(self, view: "QuestionView"):
        super().__init__(title="Your answer", timeout=600)
        self.qview = view
        self.answer = discord.ui.TextInput(label="Answer", placeholder="A number or short expression",
                                           max_length=60, required=True)
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.qview.resolve(interaction, self.answer.value)


class _AnswerButton(discord.ui.Button):
    def __init__(self, view: "QuestionView"):
        super().__init__(label="Submit answer", style=discord.ButtonStyle.primary, emoji="✍️", row=0)
        self.qview = view

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.send_modal(_AnswerModal(self.qview))


class QuestionView(BaseView):
    def __init__(self, bot, owner_id: int, question: Question, *, title: str, color: int,
                 key: Optional[str] = None, source: str = "practice", points: int = 0,
                 footer: Optional[str] = None, back: Optional[Callback] = None,
                 back_label: str = "Back", next_cb: Optional[Callback] = None,
                 next_label: str = "Next question", timeout: float = 900,
                 diagram_png: Optional[bytes] = None):
        super().__init__(owner_id, timeout=timeout)
        self.bot, self.q = bot, question
        self.title, self.color, self.key, self.source, self.points = title, color, key, source, points
        self.footer, self.back, self.next_cb, self.next_label = footer, back, next_cb, next_label
        self.answered = False
        self.diagram_png = diagram_png
        if question.is_mcq:
            for i in range(len(question.options or ())):
                self.add_item(_OptionButton(i, self))
        else:
            self.add_item(_AnswerButton(self))
        if back is not None:
            self.add_item(CallbackButton(back_label, self._go_back, emoji="◀️", row=1))

    def files(self) -> list[discord.File]:
        """A fresh File each time (File objects are consumed when sent)."""
        if not self.diagram_png:
            return []
        return [discord.File(io.BytesIO(self.diagram_png), filename="diagram.png")]

    def _decorate(self, embed: discord.Embed) -> discord.Embed:
        if self.diagram_png:
            embed.set_image(url="attachment://diagram.png")
        return embed

    def embed(self) -> discord.Embed:
        return self._decorate(question_embed(self.q, title=self.title, color=self.color,
                                             points=self.points, footer=self.footer))

    async def show(self, interaction: discord.Interaction, *, edit: bool = True,
                   replaces: Optional[discord.ui.View] = None) -> None:
        await present(interaction, embed=self.embed(), view=self, files=self.files(), edit=edit,
                      replaces=replaces)

    async def _go_back(self, interaction: discord.Interaction) -> None:
        assert self.back is not None
        self.stop()
        await self.back(interaction)

    async def _go_next(self, interaction: discord.Interaction) -> None:
        assert self.next_cb is not None
        self.stop()
        await self.next_cb(interaction)

    async def resolve(self, interaction: discord.Interaction, response) -> None:
        if self.answered:
            await interaction.response.send_message("You already answered this one.", ephemeral=True)
            return
        self.answered = True
        correct = check_answer(self.q, response)
        awarded, first = 0, True
        if self.key and self.points:
            try:
                first, awarded = await self.bot.db.record_attempt(
                    interaction.user.id, self.key, self.source, correct, self.points)
            except Exception:  # noqa: BLE001
                log.exception("Could not record attempt for %s", self.key)
                first, awarded = False, 0

        if correct and awarded:
            result = f"✅ **Correct!** +{awarded} points"
        elif correct and self.key and not first:
            result = "✅ **Correct.** You had already attempted this question, so no extra points."
        elif correct:
            result = "✅ **Correct!** (practice: no points)"
        else:
            result = f"❌ **Not quite.** Answer: **{self.q.correct_display()}**"
        if self.key and not correct and not first:
            result += "\nAlready attempted before: no points change."

        for item in self.children:
            if isinstance(item, _OptionButton):
                item.disabled = True
                if item.index == self.q.answer_index:
                    item.style = discord.ButtonStyle.success
                elif item.index == response:
                    item.style = discord.ButtonStyle.danger
            elif isinstance(item, _AnswerButton):
                item.disabled = True
        if self.next_cb is not None:
            self.add_item(CallbackButton(self.next_label, self._go_next, style=discord.ButtonStyle.primary,
                                         emoji="▶️", row=1))
        embed = question_embed(self.q, title=self.title, color=self.color, points=self.points,
                               footer=self.footer, result=result, correct=correct)
        await present(interaction, embed=self._decorate(embed), view=self, files=self.files(), edit=True)
