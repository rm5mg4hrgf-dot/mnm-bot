"""Daily challenge and the weekly interdisciplinary MNM Championship."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from quiz_ui import QuestionView
from services.challenge_service import (
    CHAMPIONSHIP_MULTIPLIER, DISCIPLINE_NAMES, Question, championship_points, next_utc_midnight, today_utc,
    week_id, week_start_iso,
)
from ui import BRAND, WARNING, BaseView, CallbackButton, make_embed, present

log = logging.getLogger("mnm.challenges")

DISC_COLOR = {"neuro": 0x2EC4B6, "math": 0x8B5CF6, "med": 0xEF476F}
DISC_EMOJI = {"neuro": "🧠", "math": "📐", "med": "🧬"}


class ChallengesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def bank(self):
        return self.bot.content.bank  # type: ignore[attr-defined]

    @property
    def db(self):
        return self.bot.db  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ hub
    async def hub_embed(self, user_id: int) -> discord.Embed:
        today = today_utc()
        daily = self.bank.daily(today)
        wid = week_id(today)
        daily_done = await self.db.get_attempt(user_id, f"daily:{today.isoformat()}")
        keys = {f"champ:{wid}:{q.id}" for q in self.bank.championship(wid)}
        answered = len(keys & await self.db.attempted_keys(user_id, f"champ:{wid}:"))
        reset = next_utc_midnight()
        return make_embed("🏆 MNM Challenges", "Earn points, climb the leaderboard and sharpen your thinking.",
                          color=BRAND, fields=[
            (f"{DISC_EMOJI[daily.discipline]} Daily challenge",
             f"Today: **{DISCIPLINE_NAMES[daily.discipline]}**, {daily.difficulty} ({daily.points} pts)\n"
             f"Status: {'✅ attempted' if daily_done else '🕒 available'}\nNew one <t:{int(reset.timestamp())}:R>", True),
            ("🌐 Championship",
             f"Week **{wid}** · interdisciplinary\n{answered}/{len(keys)} questions answered\n"
             f"Points are ×{CHAMPIONSHIP_MULTIPLIER}", True),
        ])

    async def show_hub(self, interaction: discord.Interaction, *, edit: bool = False,
                       replaces: Optional[discord.ui.View] = None) -> None:
        view = BaseView(interaction.user.id, timeout=600)

        async def daily(i: discord.Interaction) -> None:
            view.stop()
            await self.show_daily(i)

        async def champ(i: discord.Interaction) -> None:
            view.stop()
            await self.show_championship(i)

        async def stats(i: discord.Interaction) -> None:
            await self.show_stats(i)

        view.add_item(CallbackButton("Daily challenge", daily, style=discord.ButtonStyle.primary, emoji="📅"))
        view.add_item(CallbackButton("Championship", champ, style=discord.ButtonStyle.primary, emoji="🌐"))
        view.add_item(CallbackButton("My stats", stats, emoji="📊"))
        await present(interaction, embed=await self.hub_embed(interaction.user.id), view=view, edit=edit,
                      replaces=replaces)

    async def _back_to_hub(self, interaction: discord.Interaction) -> None:
        await self.show_hub(interaction, edit=True)

    # --------------------------------------------------------------- daily
    async def show_daily(self, interaction: discord.Interaction) -> None:
        today = today_utc()
        q = self.bank.daily(today)
        key = f"daily:{today.isoformat()}"
        prior = await self.db.get_attempt(interaction.user.id, key)
        if prior is not None:
            result = (f"✅ You solved today's challenge (+{prior['points']} pts)." if prior["correct"]
                      else f"❌ You attempted today's challenge. Answer: **{q.correct_display()}**.")
            embed = make_embed("📅 Daily challenge", f"{q.text}\n\n{result}\n\n**Explanation:** {q.explanation}",
                               color=WARNING)
            view = BaseView(interaction.user.id, timeout=300)
            view.add_item(CallbackButton("Back", self._back_to_hub, emoji="◀️"))
            await present(interaction, embed=embed, view=view, edit=True)
            return
        view = QuestionView(self.bot, interaction.user.id, q, title=f"📅 Daily challenge · {today:%d %b}",
                            color=DISC_COLOR[q.discipline], key=key, source="daily", points=q.points,
                            footer="one attempt", back=self._back_to_hub, back_label="Back")
        await view.show(interaction, edit=True)

    # --------------------------------------------------------- championship
    async def show_championship(self, interaction: discord.Interaction) -> None:
        today = today_utc()
        wid = week_id(today)
        questions = self.bank.championship(wid)
        done = await self.db.attempted_keys(interaction.user.id, f"champ:{wid}:")
        remaining = [q for q in questions if f"champ:{wid}:{q.id}" not in done]
        stats = await self.db.user_stats(interaction.user.id, week_start_iso(today), f"champ:{wid}:")
        if not remaining:
            embed = make_embed(
                f"🌐 Championship · {wid}",
                f"You answered all {len(questions)} questions this week. 🎉\n\n"
                f"Championship points: **{stats['total']}**" + (f" · rank **#{stats['rank']}**" if stats["rank"] else "")
                + "\nA new set opens on Monday (UTC).", color=BRAND)
            view = BaseView(interaction.user.id, timeout=300)
            view.add_item(CallbackButton("Back", self._back_to_hub, emoji="◀️"))
            await present(interaction, embed=embed, view=view, edit=True)
            return
        await self._championship_question(interaction, wid, remaining[0], len(questions), len(done & {
            f"champ:{wid}:{q.id}" for q in questions}))

    async def _championship_question(self, interaction: discord.Interaction, wid: str, q: Question,
                                     total: int, answered: int) -> None:
        async def next_cb(i: discord.Interaction) -> None:
            await self.show_championship(i)

        view = QuestionView(
            self.bot, interaction.user.id, q, title=f"🌐 Championship · question {answered + 1}/{total}",
            color=DISC_COLOR[q.discipline], key=f"champ:{wid}:{q.id}", source="championship",
            points=championship_points(q), footer=f"week {wid} · one attempt per question",
            back=self._back_to_hub, back_label="Back", next_cb=next_cb)
        await view.show(interaction, edit=True)

    # ---------------------------------------------------------------- stats
    async def show_stats(self, interaction: discord.Interaction) -> None:
        today = today_utc()
        allt = await self.db.user_stats(interaction.user.id)
        week = await self.db.user_stats(interaction.user.id, week_start_iso(today))
        embed = make_embed(f"📊 {interaction.user.display_name}", None, color=BRAND, fields=[
            ("All-time", f"**{allt['total']}** pts\nRank: {'#' + str(allt['rank']) if allt['rank'] else '—'}\n"
                         f"Correct answers: {allt['solved']}", True),
            ("This week", f"**{week['total']}** pts\nRank: {'#' + str(week['rank']) if week['rank'] else '—'}\n"
                          f"Correct answers: {week['solved']}", True),
        ])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="challenge", description="Daily challenge, weekly championship and your stats.")
    async def challenge(self, interaction: discord.Interaction) -> None:
        await self.show_hub(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChallengesCog(bot))
