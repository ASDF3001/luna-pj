import re
from datetime import datetime, timedelta, timezone

import discord

from db import get_settings


def color_from_hex(value: str) -> discord.Color:
    value = value.strip().replace("#", "")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError("HEXは #RRGGBB または RRGGBB 形式で指定してください。")
    return discord.Color(int(value, 16))


def make_embed(title: str, description: str, color=discord.Color.blurple(),
               footer: str | None = None) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color,
                      timestamp=datetime.now(timezone.utc))
    if footer:
        e.set_footer(text=footer)
    return e


async def respond(interaction: discord.Interaction, *, embed=None, content=None,
                  ephemeral: bool = False, view=None):
    kwargs: dict = {"ephemeral": ephemeral}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view

    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


def can_act_on(member: discord.Member) -> bool:
    me = member.guild.me
    if member.id == me.id:
        return False
    if member.id == member.guild.owner_id:
        return False
    if member.top_role >= me.top_role:
        return False
    return True


def role_is_dangerous(role: discord.Role) -> bool:
    p = role.permissions
    return bool(
        p.administrator or p.manage_guild or p.manage_roles
        or p.manage_channels or p.ban_members or p.kick_members
        or p.mention_everyone
    )


async def find_audit_user(guild: discord.Guild, action: discord.AuditLogAction,
                          target_id: int | None = None,
                          window: float = 10.0) -> discord.abc.User | None:
    try:
        async for entry in guild.audit_logs(limit=8, action=action):
            if target_id is not None:
                target = entry.target
                if not target or getattr(target, "id", None) != target_id:
                    continue
            age = (discord.utils.utcnow() - entry.created_at).total_seconds()
            if age <= window:
                return entry.user
    except discord.Forbidden:
        return None
    return None


async def punishment(member: discord.Member, action: str, reason: str) -> str:
    action = (action or "delete").lower()

    if action == "none":
        return "none"

    if not can_act_on(member):
        return "skipped"

    perms = member.guild.me.guild_permissions

    try:
        if action == "kick":
            if not perms.kick_members:
                return "no_permission"
            await member.kick(reason=reason)
            return "kick"
        if action == "ban":
            if not perms.ban_members:
                return "no_permission"
            await member.ban(reason=reason, delete_message_days=0)
            return "ban"
        if action in ("timeout", "mute"):
            if not perms.moderate_members:
                return "no_permission"
            await member.timeout(discord.utils.utcnow() + timedelta(minutes=10),
                                 reason=reason)
            return "timeout"
    except discord.HTTPException:
        return "failed"
    return "none"


async def report(guild: discord.Guild, title: str, description: str,
                 member: discord.Member | None = None,
                 color=discord.Color.orange()):
    s = get_settings(guild.id)
    channel_id = s["trap_channel_id"]
    if channel_id:
        channel = guild.get_channel(channel_id)
        if channel:
            if member:
                description += f"\n**対象:** {member.mention} (`{member.id}`)"
            await channel.send(embed=make_embed(title, description, color))


async def send_warning(channel: discord.abc.Messageable, title: str,
                       description: str, color=discord.Color.red()):
    try:
        await channel.send(embed=make_embed(title, description, color))
    except discord.HTTPException:
        pass
