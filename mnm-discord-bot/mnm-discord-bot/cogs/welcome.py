"""Personalised welcome messages for new members."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from config import TAGLINE, WEBSITE_URL
from ui import BRAND, make_embed, send_error

log = logging.getLogger("mnm.welcome")


class WelcomeView(discord.ui.View):
    """Persistent view attached to every welcome message."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Website", url=WEBSITE_URL, style=discord.ButtonStyle.link))

    @discord.ui.button(label="Choose interests", style=discord.ButtonStyle.primary, emoji="🎓",
                       custom_id="mnm:welcome:roles")
    async def choose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("RolesCog")
        if cog is None:
            await send_error(interaction, "Roles are unavailable right now.")
            return
        await cog.open_picker(interaction)


class WelcomeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(WelcomeView())

    def build_embed(self, member: discord.Member) -> discord.Embed:
        cfg = self.bot.config  # type: ignore[attr-defined]
        rules = f"<#{cfg.rules_channel_id}>" if cfg.rules_channel_id else "the rules channel"
        tracks = f"<#{cfg.tracks_channel_id}>" if cfg.tracks_channel_id else "the announcements"
        created = int(member.created_at.timestamp())
        embed = make_embed(
            f"Welcome to MediNeuroMatics, {member.display_name}!",
            f"*{TAGLINE}*\n\nMNM is a student STEM community for curious minds who want to learn, "
            "compete, research and build across three disciplines.",
            color=BRAND,
            fields=[
                ("🧠 Neuroscience", "The brain, neurons, memory and behaviour.", True),
                ("🧬 Medicine", "Biology, anatomy, physiology and medical science.", True),
                ("📐 Mathematics", "Olympiad problem solving, statistics and modelling.", True),
                ("Start here", f"1. Read {rules}\n2. Pick your interests with the button below\n"
                               f"3. Explore tracks in {tracks}\n4. Try `/lab`, `/challenge` and `/ask`", False),
                ("Member", f"You are member **#{member.guild.member_count}**\nAccount created <t:{created}:R>", False),
            ],
            thumbnail=member.display_avatar.url,
        )
        return embed

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = self.bot.config  # type: ignore[attr-defined]
        if member.guild.id != cfg.guild_id or member.bot:
            return
        channel = await self.bot.get_channel_safe(cfg.welcome_channel_id)  # type: ignore[attr-defined]
        if channel is None:
            return
        try:
            await channel.send(content=member.mention, embed=self.build_embed(member), view=WelcomeView(),
                               allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.HTTPException:
            log.warning("Could not send the welcome message", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
