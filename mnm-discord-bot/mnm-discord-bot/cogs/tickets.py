"""Support tickets: private channels, close button and stored transcripts."""
from __future__ import annotations

import asyncio
import io
import logging
import re
from collections import defaultdict
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from permissions import is_staff
from ui import BRAND, BaseView, ERROR, WARNING, make_embed, present, send_error, success_embed

log = logging.getLogger("mnm.tickets")

CATEGORIES = {
    "general": ("General Support", "💬", "Questions about MNM, the server or the bot."),
    "applications": ("Applications", "📝", "Joining a team or programme."),
    "partnerships": ("Partnerships", "🤝", "Collaboration and partnership proposals."),
    "other": ("Other Enquiries", "📨", "Anything else."),
}
MAX_TRANSCRIPT_MESSAGES = 2000
MAX_TRANSCRIPT_CHARS = 300_000


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:20] or "member"


class TicketSelect(discord.ui.Select):
    def __init__(self, custom_id: Optional[str] = None):
        options = [discord.SelectOption(label=n, value=k, emoji=e, description=d)
                   for k, (n, e, d) in CATEGORIES.items()]
        kwargs = {"custom_id": custom_id} if custom_id else {}
        super().__init__(placeholder="Choose a ticket category…", options=options, min_values=1, max_values=1,
                         **kwargs)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        cog: Optional["TicketsCog"] = interaction.client.get_cog("TicketsCog")  # type: ignore[assignment]
        if cog is None:
            await send_error(interaction, "Tickets are unavailable right now.")
            return
        await cog.create_ticket(interaction, self.values[0])
        # Reset the public panel's select so it doesn't keep showing the last choice.
        message = interaction.message
        if message is not None and not message.flags.ephemeral and self.custom_id == "mnm:ticket:open":
            try:
                await message.edit(view=TicketPanelView())
            except discord.HTTPException:
                pass


class TicketPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketSelect(custom_id="mnm:ticket:open"))


class TicketPickerView(BaseView):
    def __init__(self, owner_id: int):
        super().__init__(owner_id, timeout=120)
        self.add_item(TicketSelect())


class ConfirmCloseView(BaseView):
    def __init__(self, owner_id: int):
        super().__init__(owner_id, timeout=30)
        self.confirmed = False

    @discord.ui.button(label="Yes, close ticket", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        await interaction.response.edit_message(content="Closing…", embed=None, view=None)
        self.stop()
        cog: Optional["TicketsCog"] = interaction.client.get_cog("TicketsCog")  # type: ignore[assignment]
        if cog is not None and isinstance(interaction.channel, discord.TextChannel):
            await cog.close_ticket(interaction.channel, interaction.user)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)
        self.stop()


class TicketControlView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒",
                       custom_id="mnm:ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog: Optional["TicketsCog"] = interaction.client.get_cog("TicketsCog")  # type: ignore[assignment]
        if cog is None or interaction.channel is None:
            await send_error(interaction, "Tickets are unavailable right now.")
            return
        row = await interaction.client.db.ticket_by_channel(interaction.channel.id)  # type: ignore[attr-defined]
        if row is None or row["status"] != "open":
            await send_error(interaction, "This is not an open ticket.")
            return
        cfg = interaction.client.config  # type: ignore[attr-defined]
        if interaction.user.id != row["user_id"] and not is_staff(interaction.user, cfg):
            await send_error(interaction, "Only the ticket owner or MNM staff can close this ticket.")
            return
        await interaction.response.send_message(
            "Close this ticket? A transcript will be saved for staff.", view=ConfirmCloseView(interaction.user.id),
            ephemeral=True)


class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def cog_load(self) -> None:
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketControlView())

    # ----------------------------------------------------------- creation
    async def create_ticket(self, interaction: discord.Interaction, category_key: str) -> None:
        cfg = self.bot.config  # type: ignore[attr-defined]
        db = self.bot.db  # type: ignore[attr-defined]
        guild, member = interaction.guild, interaction.user
        if guild is None or not isinstance(member, discord.Member) or category_key not in CATEGORIES:
            await send_error(interaction, "Tickets can only be opened inside the server.")
            return
        await interaction.response.defer(ephemeral=True)
        category = guild.get_channel(cfg.ticket_category_id) if cfg.ticket_category_id else None
        if not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send(embed=make_embed(
                "Tickets unavailable", "The ticket category isn't configured. Please contact an administrator.",
                color=ERROR), ephemeral=True)
            return

        async with self._locks[member.id]:
            existing = await db.open_ticket_for_user(member.id)
            if existing is not None:
                channel = guild.get_channel(existing["channel_id"])
                if channel is not None:
                    await interaction.followup.send(embed=make_embed(
                        "You already have an open ticket", f"Please continue in {channel.mention}.",
                        color=WARNING), ephemeral=True)
                    return
                await db.close_ticket(existing["channel_id"], None, None, status="abandoned")

            name, emoji, _ = CATEGORIES[category_key]
            overwrites: dict = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                    read_message_history=True, attach_files=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                      manage_channels=True, read_message_history=True,
                                                      embed_links=True, attach_files=True),
            }
            staff_roles = []
            for rid in (cfg.admin_role_id, cfg.moderator_role_id):
                role = guild.get_role(rid) if rid else None
                if role is not None:
                    staff_roles.append(role)
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                                   read_message_history=True, attach_files=True)
            try:
                channel = await guild.create_text_channel(
                    f"{category_key}-{_slug(member.display_name)}", category=category, overwrites=overwrites,
                    topic=f"{name} ticket for {member} ({member.id})", reason=f"Ticket opened by {member}")
            except discord.Forbidden:
                await interaction.followup.send(embed=make_embed(
                    "Couldn't create the ticket", "I'm missing the Manage Channels permission.", color=ERROR),
                    ephemeral=True)
                return
            except discord.HTTPException:
                log.exception("Ticket channel creation failed")
                await interaction.followup.send(embed=make_embed(
                    "Couldn't create the ticket", "Discord returned an error. Please try again.", color=ERROR),
                    ephemeral=True)
                return
            try:
                ticket_id = await db.create_ticket(channel.id, member.id, category_key)
            except Exception:  # noqa: BLE001
                log.exception("Could not store ticket; removing channel")
                await channel.delete(reason="Ticket storage failed")
                await interaction.followup.send(embed=make_embed(
                    "Couldn't create the ticket", "Internal error. Please try again.", color=ERROR), ephemeral=True)
                return

        intro = make_embed(
            f"{emoji} {name} · Ticket #{ticket_id}",
            f"Hello {member.mention}! Describe your question and an MNM team member will reply here.\n\n"
            "Only you and MNM staff can see this channel. Use **Close ticket** when you're done.",
            color=BRAND)
        mentions = " ".join(r.mention for r in staff_roles)
        await channel.send(content=mentions or None, embed=intro, view=TicketControlView(),
                           allowed_mentions=discord.AllowedMentions(roles=staff_roles, users=[member]))
        await interaction.followup.send(embed=success_embed(f"Your ticket is ready: {channel.mention}",
                                                            "Ticket created"), ephemeral=True)
        await self.bot.log_event(make_embed(  # type: ignore[attr-defined]
            "🎫 Ticket opened", f"#{ticket_id} · {name}\nBy {member.mention} in {channel.mention}", color=BRAND))

    # -------------------------------------------------------------- closing
    async def _transcript(self, channel: discord.TextChannel) -> str:
        lines: list[str] = [f"Transcript for #{channel.name} ({channel.id})", "=" * 60]
        count = 0
        async for msg in channel.history(limit=MAX_TRANSCRIPT_MESSAGES, oldest_first=True):
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            body = msg.content or ""
            for att in msg.attachments:
                body += f"\n  [attachment] {att.filename} {att.url}"
            for emb in msg.embeds:
                if emb.title or emb.description:
                    body += f"\n  [embed] {emb.title or ''} {(emb.description or '')[:300]}"
            lines.append(f"[{ts}] {msg.author} ({msg.author.id}): {body}")
            count += 1
        lines.append("=" * 60)
        lines.append(f"{count} messages")
        text = "\n".join(lines)
        if len(text) > MAX_TRANSCRIPT_CHARS:
            text = text[:MAX_TRANSCRIPT_CHARS] + "\n[transcript truncated]"
        return text

    async def close_ticket(self, channel: discord.TextChannel, closer: discord.abc.User) -> None:
        db = self.bot.db  # type: ignore[attr-defined]
        row = await db.ticket_by_channel(channel.id)
        if row is None or row["status"] != "open":
            return
        transcript = await self._transcript(channel)
        saved = await db.close_ticket(channel.id, closer.id, transcript)
        if not saved:
            return
        file = discord.File(io.BytesIO(transcript.encode("utf-8")), filename=f"ticket-{row['id']}-transcript.txt")
        await self.bot.log_event(make_embed(  # type: ignore[attr-defined]
            "🔒 Ticket closed",
            f"#{row['id']} · {CATEGORIES.get(row['category'], ('?',))[0]}\nOpened by <@{row['user_id']}> · "
            f"closed by {closer.mention}", color=WARNING), file=file)
        try:
            await channel.send("🔒 Ticket closed. This channel will be deleted in 5 seconds.")
            await asyncio.sleep(5)
            await channel.delete(reason=f"Ticket closed by {closer}")
        except discord.HTTPException:
            log.warning("Could not delete ticket channel %s", channel.id, exc_info=True)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        row = await self.bot.db.ticket_by_channel(channel.id)  # type: ignore[attr-defined]
        if row is not None and row["status"] == "open":
            await self.bot.db.close_ticket(channel.id, None, None, status="abandoned")  # type: ignore[attr-defined]

    # ------------------------------------------------------------- commands
    @app_commands.command(name="ticket", description="Open a private support ticket with the MNM team.")
    @app_commands.guild_only()
    async def ticket(self, interaction: discord.Interaction) -> None:
        await present(interaction, embed=make_embed(
            "🎫 Open a ticket", "Choose a category below. A private channel will be created for you.",
            color=BRAND), view=TicketPickerView(interaction.user.id))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketsCog(bot))
