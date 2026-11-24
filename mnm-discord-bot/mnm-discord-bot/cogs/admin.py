"""Administrator tools under the single /mnm command group."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.roles import TEAM_ROLES, role_is_safe, panel_embed as roles_panel_embed, RolePanelView
from cogs.tickets import CATEGORIES, TicketPanelView
from permissions import admin_only
from services.moderation_service import normalise_filter_word
from services.research_service import OPPORTUNITY_CATEGORIES, ValidationError, validate_opportunity
from ui import BRAND, ERROR, make_embed, success_embed

log = logging.getLogger("mnm.admin")

TEAM_CHOICES = [app_commands.Choice(name=name, value=key) for key, name in TEAM_ROLES]
TEAM_NAMES = dict(TEAM_ROLES)


def _tick(flag: bool) -> str:
    return "✅" if flag else "⚠️ not set"


class AdminCog(commands.Cog):
    mnm = app_commands.Group(name="mnm", description="MNM administration (admins only).", guild_only=True,
                             default_permissions=discord.Permissions(administrator=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------------------------------------------------------- panels
    @mnm.command(name="panel", description="Post a ticket or role-selection panel in a channel.")
    @admin_only()
    @app_commands.describe(kind="Which panel to post", channel="Where to post it (default: this channel)")
    @app_commands.choices(kind=[app_commands.Choice(name="Tickets", value="tickets"),
                                app_commands.Choice(name="Interest roles", value="roles")])
    async def panel(self, interaction: discord.Interaction, kind: app_commands.Choice[str],
                    channel: Optional[discord.TextChannel] = None) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Choose a text channel.", ephemeral=True)
            return
        perms = target.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"I need View Channel, Send Messages and Embed Links in {target.mention}.", ephemeral=True)
            return
        if kind.value == "tickets":
            lines = "\n".join(f"{e} **{n}**: {d}" for n, e, d in CATEGORIES.values())
            embed = make_embed("🎫 MNM Support", "Need help? Choose a category to open a private ticket "
                                                "with the MNM team.\n\n" + lines, color=BRAND)
            await target.send(embed=embed, view=TicketPanelView())
        else:
            if not await self.bot.db.roles("interest"):  # type: ignore[attr-defined]
                await interaction.response.send_message("Run `/mnm setup-roles` first.", ephemeral=True)
                return
            await target.send(embed=roles_panel_embed(), view=RolePanelView())
        await interaction.response.send_message(embed=success_embed(f"Panel posted in {target.mention}."),
                                                ephemeral=True)

    @mnm.command(name="setup-roles", description="Create or link the six academic interest roles.")
    @admin_only()
    async def setup_roles(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        cog = self.bot.get_cog("RolesCog")
        try:
            created, reused = await cog.setup_interest_roles(interaction.guild)  # type: ignore[union-attr]
        except discord.Forbidden:
            await interaction.followup.send("I need the Manage Roles permission.", ephemeral=True)
            return
        text = (f"Created: {', '.join(created) or 'none'}\nLinked existing: {', '.join(reused) or 'none'}\n\n"
                "Make sure my role sits **above** these roles in Server Settings → Roles.")
        await interaction.followup.send(embed=success_embed(text, "Interest roles ready"), ephemeral=True)

    # ------------------------------------------------------------ team roles
    @mnm.command(name="team-role", description="Link an MNM team to an existing Discord role.")
    @admin_only()
    @app_commands.choices(team=TEAM_CHOICES)
    async def team_role(self, interaction: discord.Interaction, team: app_commands.Choice[str],
                        role: discord.Role) -> None:
        if not role_is_safe(role):
            await interaction.response.send_message(
                "That role has moderation-level permissions, so it can't be managed by this command.",
                ephemeral=True)
            return
        await self.bot.db.set_role("team", team.value, role.id)  # type: ignore[attr-defined]
        await interaction.response.send_message(embed=success_embed(f"**{team.name}** → {role.mention}"),
                                                ephemeral=True)

    @mnm.command(name="team-assign", description="Give or remove an MNM team role for a member.")
    @admin_only()
    @app_commands.choices(team=TEAM_CHOICES)
    async def team_assign(self, interaction: discord.Interaction, member: discord.Member,
                          team: app_commands.Choice[str], remove: bool = False) -> None:
        mapping = await self.bot.db.roles("team")  # type: ignore[attr-defined]
        role = interaction.guild.get_role(mapping.get(team.value, 0))
        if role is None:
            await interaction.response.send_message(
                f"No role is linked to {team.name}. Use `/mnm team-role` first.", ephemeral=True)
            return
        actor = interaction.user
        if (not role_is_safe(role) or role >= interaction.guild.me.top_role
                or (actor.id != interaction.guild.owner_id and role >= actor.top_role)):  # type: ignore[union-attr]
            await interaction.response.send_message("I (or you) can't manage that role: check the role order.",
                                                    ephemeral=True)
            return
        try:
            if remove:
                await member.remove_roles(role, reason=f"MNM team role removed by {actor}")
            else:
                await member.add_roles(role, reason=f"MNM team role assigned by {actor}")
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to change that member's roles.",
                                                    ephemeral=True)
            return
        verb = "Removed" if remove else "Assigned"
        await interaction.response.send_message(embed=success_embed(
            f"{verb} **{team.name}** for {member.mention}."), ephemeral=True)

    # --------------------------------------------------------- opportunities
    @mnm.command(name="opportunity-add", description="Add a verified opportunity link.")
    @admin_only()
    @app_commands.choices(category=[app_commands.Choice(name=v, value=k)
                                    for k, v in OPPORTUNITY_CATEGORIES.items()])
    async def opportunity_add(self, interaction: discord.Interaction, title: str, url: str,
                              category: app_commands.Choice[str], description: Optional[str] = None) -> None:
        try:
            t, u, c, d = validate_opportunity(title, url, category.value, description)
        except ValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        oid = await self.bot.db.add_opportunity(t, u, c, d, interaction.user.id)  # type: ignore[attr-defined]
        await interaction.response.send_message(embed=success_embed(f"Added **{t}** (id `{oid}`)."), ephemeral=True)

    @mnm.command(name="opportunity-remove", description="Remove an opportunity by its id.")
    @admin_only()
    async def opportunity_remove(self, interaction: discord.Interaction, opportunity_id: int) -> None:
        removed = await self.bot.db.remove_opportunity(opportunity_id)  # type: ignore[attr-defined]
        await interaction.response.send_message(
            embed=success_embed("Removed.") if removed else make_embed("Not found", "No such id.", color=ERROR),
            ephemeral=True)

    # ---------------------------------------------------------------- filter
    @mnm.command(name="filter-add", description="Add a word or phrase to the content filter.")
    @admin_only()
    async def filter_add(self, interaction: discord.Interaction, word: str) -> None:
        try:
            clean = normalise_filter_word(word)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        added = await self.bot.db.add_filter_word(clean)  # type: ignore[attr-defined]
        await self.bot.get_cog("ModerationCog").reload_filter()  # type: ignore[union-attr]
        await interaction.response.send_message("Added." if added else "Already in the filter.", ephemeral=True)

    @mnm.command(name="filter-remove", description="Remove a word from the content filter.")
    @admin_only()
    async def filter_remove(self, interaction: discord.Interaction, word: str) -> None:
        removed = await self.bot.db.remove_filter_word(word.strip().lower())  # type: ignore[attr-defined]
        await self.bot.get_cog("ModerationCog").reload_filter()  # type: ignore[union-attr]
        await interaction.response.send_message("Removed." if removed else "Not in the filter.", ephemeral=True)

    @mnm.command(name="filter-list", description="List filtered words (visible only to you).")
    @admin_only()
    async def filter_list(self, interaction: discord.Interaction) -> None:
        words = await self.bot.db.filter_words()  # type: ignore[attr-defined]
        await interaction.response.send_message(", ".join(f"`{w}`" for w in words) or "The filter list is empty.",
                                                ephemeral=True)

    # ---------------------------------------------------------------- status
    @mnm.command(name="status", description="Check the bot's configuration and health.")
    @admin_only()
    async def status(self, interaction: discord.Interaction) -> None:
        cfg = self.bot.config  # type: ignore[attr-defined]
        guild = interaction.guild
        me = guild.me
        used, users = await self.bot.db.ai_usage_today(self.bot.ai._today())  # type: ignore[attr-defined]

        def chan(cid: Optional[int]) -> str:
            ch = guild.get_channel(cid) if cid else None
            return f"{ch.mention} ✅" if ch else "⚠️ not set or not visible"

        embed = make_embed("🩺 Bot status", None, color=BRAND, fields=[
            ("Welcome channel", chan(cfg.welcome_channel_id), True),
            ("Log channel", chan(cfg.log_channel_id), True),
            ("Showcase channel", chan(cfg.showcase_channel_id), True),
            ("Ticket category", chan(cfg.ticket_category_id), True),
            ("AI assistant", f"{'enabled' if self.bot.ai.enabled else 'disabled'}"  # type: ignore[attr-defined]
                             f"\nToday: {used}/{cfg.ai_global_daily_limit} calls ({users} users)", True),
            ("Permissions", f"Manage Roles {_tick(me.guild_permissions.manage_roles)}\n"
                            f"Manage Channels {_tick(me.guild_permissions.manage_channels)}\n"
                            f"Moderate Members {_tick(me.guild_permissions.moderate_members)}\n"
                            f"Ban Members {_tick(me.guild_permissions.ban_members)}", True),
            ("Latency", f"{self.bot.latency * 1000:.0f} ms", True),
        ])
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
