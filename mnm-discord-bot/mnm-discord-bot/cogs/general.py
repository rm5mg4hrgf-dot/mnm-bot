"""/help and the Opportunities Hub."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import TAGLINE, WEBSITE_URL
from services.research_service import OPPORTUNITY_CATEGORIES
from ui import BRAND, BaseView, make_embed, present, website_button

log = logging.getLogger("mnm.general")


class GeneralCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="See what the MNM bot can do.")
    async def help(self, interaction: discord.Interaction) -> None:
        ai_note = "" if self.bot.ai.enabled else " *(not enabled yet)*"  # type: ignore[attr-defined]
        embed = make_embed(
            "🧪 MediNeuroMatics Bot",
            f"*{TAGLINE}*\n\nEverything works through menus and buttons: you only need a handful of commands.",
            color=BRAND, fields=[
                ("🔬 `/lab`", "Neuron Lab (🧠), Theorem Lab (📐) and Vitalis Lab (🧬): atlases, simulators, "
                              "quizzes, trials and tools.", False),
                (f"🤖 `/ask`{ai_note}", "AI academic assistant with Neuro, Math and Med modes. Follow-up "
                                        "questions supported.", False),
                ("🏆 `/challenge` · `/leaderboard`", "Daily challenge, weekly interdisciplinary championship "
                                                    "and rankings.", False),
                ("🎓 `/roles`", "Choose your academic interest roles.", True),
                ("🔬 `/showcase`", "Share a research project or build.", True),
                ("🌍 `/opportunities`", "Official MNM opportunities hub.", True),
                ("🎫 `/ticket`", "Contact the MNM team privately.", True),
            ])
        view = BaseView(timeout=300)
        view.add_item(website_button("MNM website"))
        await present(interaction, embed=embed, view=view)

    @app_commands.command(name="opportunities", description="Competitions, olympiads, research and programmes.")
    async def opportunities(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.opportunities(25)  # type: ignore[attr-defined]
        embed = make_embed(
            "🌍 Opportunities Hub",
            f"The official MNM opportunities hub lives on the website:\n**{WEBSITE_URL}**\n\n"
            "Below are links verified by MNM administrators.", color=BRAND)
        grouped: dict[str, list[str]] = {}
        for r in rows:
            line = f"• [{r['title']}]({r['url']})" + (f"\n  {r['description']}" if r["description"] else "")
            grouped.setdefault(r["category"], []).append(line)
        if not grouped:
            embed.add_field(name="Verified links", value="None have been added yet. Please check the website.",
                            inline=False)
        for cat, lines in grouped.items():
            embed.add_field(name=OPPORTUNITY_CATEGORIES.get(cat, cat.title()), value="\n".join(lines)[:1024],
                            inline=False)
        view = BaseView(timeout=300)
        view.add_item(website_button("Open the hub"))
        await present(interaction, embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GeneralCog(bot))
