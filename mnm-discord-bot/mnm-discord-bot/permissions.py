"""Permission helpers for slash commands and buttons."""
from __future__ import annotations

import discord
from discord import app_commands

from config import Config, has_admin_access, has_staff_access


def _roles_of(user: discord.abc.User) -> tuple[set[int], bool]:
    if not isinstance(user, discord.Member):
        return set(), False
    return {r.id for r in user.roles}, user.guild_permissions.administrator


def is_staff(user: discord.abc.User, config: Config) -> bool:
    roles, admin = _roles_of(user)
    return has_staff_access(roles, admin, config)


def is_admin(user: discord.abc.User, config: Config) -> bool:
    roles, admin = _roles_of(user)
    return has_admin_access(roles, admin, config)


def staff_only():
    """App-command check: administrators, the admin role or the moderator role."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_staff(interaction.user, interaction.client.config):  # type: ignore[attr-defined]
            return True
        raise app_commands.CheckFailure("This command is for MNM staff only.")
    return app_commands.check(predicate)


def admin_only():
    """App-command check: administrators or the configured admin role."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_admin(interaction.user, interaction.client.config):  # type: ignore[attr-defined]
            return True
        raise app_commands.CheckFailure("This command is for MNM administrators only.")
    return app_commands.check(predicate)
