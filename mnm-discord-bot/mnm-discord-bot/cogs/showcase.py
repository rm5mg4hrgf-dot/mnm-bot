"""Research Showcase: members submit projects, staff approve them for publication."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from permissions import is_staff
from services.research_service import (
    DESCRIPTION_MAX, DESCRIPTION_MIN, DISCIPLINES, MAX_PENDING_PER_USER, TITLE_MAX, ValidationError,
    validate_submission,
)
from ui import BRAND, ERROR, SUCCESS, make_embed, send_error, success_embed

log = logging.getLogger("mnm.showcase")
DISC_COLOR = {"neuro": 0x2EC4B6, "math": 0x8B5CF6, "med": 0xEF476F, "inter": BRAND}


def submission_embed(row, author_text: str, *, status: Optional[str] = None) -> discord.Embed:
    color = DISC_COLOR.get(row["discipline"], BRAND)
    if status == "rejected":
        color = ERROR
    embed = make_embed(f"🔬 {row['title']}", row["description"], color=color,
                       footer=f"Research Showcase · {DISCIPLINES.get(row['discipline'], row['discipline'])}",
                       url=row["link"] or None)
    embed.add_field(name="Discipline", value=DISCIPLINES.get(row["discipline"], row["discipline"]), inline=True)
    embed.add_field(name="Submitted by", value=author_text, inline=True)
    if row["link"]:
        embed.add_field(name="Project link", value=row["link"], inline=False)
    return embed


class RejectModal(discord.ui.Modal, title="Reject submission"):
    reason = discord.ui.TextInput(label="Reason (sent to the member)", style=discord.TextStyle.paragraph,
                                  max_length=300, required=False)

    def __init__(self, cog: "ShowcaseCog", message: discord.Message):
        super().__init__(timeout=300)
        self.cog, self.message = cog, message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.review(interaction, self.message, approve=False, reason=self.reason.value)


class ReviewView(discord.ui.View):
    """Persistent approve/reject buttons attached to every pending submission."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    def _cog(self, interaction: discord.Interaction) -> Optional["ShowcaseCog"]:
        return interaction.client.get_cog("ShowcaseCog")  # type: ignore[return-value]

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅",
                       custom_id="mnm:showcase:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = self._cog(interaction)
        if cog is None or interaction.message is None:
            await send_error(interaction, "The showcase is unavailable right now.")
            return
        await cog.review(interaction, interaction.message, approve=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="⛔",
                       custom_id="mnm:showcase:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = self._cog(interaction)
        if cog is None or interaction.message is None:
            await send_error(interaction, "The showcase is unavailable right now.")
            return
        if not is_staff(interaction.user, interaction.client.config):  # type: ignore[attr-defined]
            await send_error(interaction, "Only MNM staff can review submissions.")
            return
        await interaction.response.send_modal(RejectModal(cog, interaction.message))


class SubmitModal(discord.ui.Modal):
    def __init__(self, cog: "ShowcaseCog", discipline: str):
        super().__init__(title=f"Showcase: {DISCIPLINES[discipline]}"[:45], timeout=900)
        self.cog, self.discipline = cog, discipline
        self.title_in = discord.ui.TextInput(label="Title", max_length=TITLE_MAX)
        self.desc_in = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, min_length=DESCRIPTION_MIN,
            max_length=DESCRIPTION_MAX, placeholder="What did you build or investigate? What did you find?")
        self.link_in = discord.ui.TextInput(label="Project link (optional)", required=False, max_length=400,
                                            placeholder="https://…")
        for item in (self.title_in, self.desc_in, self.link_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.submit(interaction, self.discipline, self.title_in.value, self.desc_in.value,
                              self.link_in.value)


class ShowcaseCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(ReviewView())

    @app_commands.command(name="showcase", description="Submit a research project or build to the MNM showcase.")
    @app_commands.describe(discipline="Which discipline does your work belong to?")
    @app_commands.choices(discipline=[app_commands.Choice(name=v, value=k) for k, v in DISCIPLINES.items()])
    @app_commands.guild_only()
    async def showcase(self, interaction: discord.Interaction, discipline: app_commands.Choice[str]) -> None:
        cfg = self.bot.config  # type: ignore[attr-defined]
        if not cfg.log_channel_id or not cfg.showcase_channel_id:
            await send_error(interaction, "The showcase isn't configured yet. Please ask an administrator.")
            return
        pending = await self.bot.db.pending_showcase_count(interaction.user.id)  # type: ignore[attr-defined]
        if pending >= MAX_PENDING_PER_USER:
            await send_error(interaction, f"You already have {pending} submissions awaiting review. "
                                          "Please wait for staff to review them.")
            return
        await interaction.response.send_modal(SubmitModal(self, discipline.value))

    async def submit(self, interaction: discord.Interaction, discipline: str, title: str, description: str,
                     link: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            title, description, link_clean = validate_submission(title, description, link)
        except ValidationError as exc:
            await send_error(interaction, str(exc))
            return
        review_channel = await self.bot.get_channel_safe(self.bot.config.log_channel_id)  # type: ignore[attr-defined]
        if review_channel is None:
            await send_error(interaction, "I couldn't reach the review channel. Please tell an administrator.")
            return
        db = self.bot.db  # type: ignore[attr-defined]
        sid = await db.create_showcase(interaction.user.id, title, description, discipline, link_clean)
        row = {"title": title, "description": description, "discipline": discipline, "link": link_clean}
        embed = submission_embed(row, interaction.user.mention)
        embed.title = f"🔬 [Pending #{sid}] {title}"[:256]
        try:
            msg = await review_channel.send(embed=embed, view=ReviewView(),
                                            allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception("Could not post submission for review")
            await interaction.followup.send(embed=make_embed(
                "Couldn't submit", "I couldn't post your submission for review. Please try again later.",
                color=ERROR), ephemeral=True)
            return
        await db.set_showcase_review_message(sid, msg.id)
        await interaction.followup.send(embed=success_embed(
            "Thanks! Your submission is with MNM staff for review. You'll be notified of the decision.",
            "Submission received"), ephemeral=True)

    async def review(self, interaction: discord.Interaction, message: discord.Message, *, approve: bool,
                     reason: str = "") -> None:
        cfg = self.bot.config  # type: ignore[attr-defined]
        if not is_staff(interaction.user, cfg):
            await send_error(interaction, "Only MNM staff can review submissions.")
            return
        db = self.bot.db  # type: ignore[attr-defined]
        row = await db.showcase_by_review_message(message.id)
        if row is None:
            await send_error(interaction, "I can't find that submission in the database.")
            return
        if row["status"] != "pending":
            await send_error(interaction, f"This submission was already {row['status']}.")
            return
        await interaction.response.defer(ephemeral=True)
        published_id = None
        if approve:
            channel = await self.bot.get_channel_safe(cfg.showcase_channel_id)
            if channel is None:
                await interaction.followup.send("I can't reach the showcase channel.", ephemeral=True)
                return
            try:
                published = await channel.send(embed=submission_embed(row, f"<@{row['user_id']}>"),
                                               allowed_mentions=discord.AllowedMentions.none())
                published_id = published.id
            except discord.HTTPException:
                log.exception("Could not publish showcase entry")
                await interaction.followup.send("Publishing failed. Check my permissions in the showcase channel.",
                                                ephemeral=True)
                return
        changed = await db.review_showcase(row["id"], "approved" if approve else "rejected",
                                           interaction.user.id, published_id)
        if not changed:
            await interaction.followup.send("Someone else already reviewed this submission.", ephemeral=True)
            return
        verdict = "approved" if approve else "rejected"
        embed = message.embeds[0].copy() if message.embeds else make_embed("Submission")
        embed.color = SUCCESS if approve else ERROR
        embed.add_field(name="Decision", value=f"{verdict.title()} by {interaction.user.mention}"
                                               + (f"\nReason: {reason}" if reason else ""), inline=False)
        try:
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            log.warning("Could not update the review message", exc_info=True)
        try:
            user = await self.bot.fetch_user(row["user_id"])
            note = (f"Your showcase submission **{row['title']}** was approved and published. 🎉" if approve else
                    f"Your showcase submission **{row['title']}** wasn't approved this time."
                    + (f"\nReason: {reason}" if reason else ""))
            await user.send(note)
        except discord.HTTPException:
            pass
        await interaction.followup.send(embed=success_embed(f"Submission {verdict}."), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShowcaseCog(bot))
