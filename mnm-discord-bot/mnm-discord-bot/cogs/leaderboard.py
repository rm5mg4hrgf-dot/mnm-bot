"""Persistent leaderboard backed by the attempts table."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.challenge_service import today_utc, week_id, week_start_iso
from ui import BRAND, BaseView, CallbackButton, make_embed, present

log = logging.getLogger("mnm.leaderboard")
MEDALS = ["🥇", "🥈", "🥉"]
TABS = {
    "all": "All-time",
    "week": "This week",
    "champ": "Championship (this week)",
}


class LeaderboardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _scope(self, tab: str) -> tuple[Optional[str], Optional[str]]:
        today = today_utc()
        if tab == "week":
            return week_start_iso(today), None
        if tab == "champ":
            return None, f"champ:{week_id(today)}:"
        return None, None

    async def build_embed(self, tab: str, user_id: int) -> discord.Embed:
        db = self.bot.db  # type: ignore[attr-defined]
        since, prefix = self._scope(tab)
        rows = await db.leaderboard(10, since, prefix)
        if rows:
            lines = [f"{MEDALS[i] if i < 3 else f'`{i + 1}.`'} <@{r['user_id']}> · **{r['total']}** pts "
                     f"· {r['solved']} correct" for i, r in enumerate(rows)]
            body = "\n".join(lines)
        else:
            body = "No points yet. Try `/challenge` or open a lab with `/lab` to be the first!"
        mine = await db.user_stats(user_id, since, prefix)
        footer = (f"Your position: #{mine['rank']} with {mine['total']} pts" if mine["rank"]
                  else "You haven't scored in this category yet")
        return make_embed(f"🏆 Leaderboard · {TABS[tab]}", body, color=BRAND, footer=footer)

    async def show(self, interaction: discord.Interaction, tab: str = "all", *, edit: bool = False,
                   replaces: Optional[discord.ui.View] = None) -> None:
        view = BaseView(interaction.user.id, timeout=300)

        async def switch(i: discord.Interaction, new_tab: str) -> None:
            await self.show(i, new_tab, edit=True, replaces=view)

        for key, label in (("all", "All-time"), ("week", "This week"), ("champ", "Championship")):
            async def cb(i: discord.Interaction, k: str = key) -> None:
                await switch(i, k)
            view.add_item(CallbackButton(label, cb, style=(discord.ButtonStyle.primary if key == tab
                                                           else discord.ButtonStyle.secondary)))
        await present(interaction, embed=await self.build_embed(tab, interaction.user.id), view=view,
                      edit=edit, replaces=replaces)

    @app_commands.command(name="leaderboard", description="See the top MNM members by challenge points.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await self.show(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeaderboardCog(bot))
