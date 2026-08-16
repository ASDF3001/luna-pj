import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from db import (
    get_settings, update_settings, is_whitelisted,
    VALID_NUKE_PUNISH, DEFAULT_NUKE_WINDOW,
)
from utils import respond, make_embed, punishment, report, find_audit_user, can_act_on


class NukeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.history: dict[str, dict[tuple[int, int], deque]] = {
            "channel": defaultdict(deque),
            "kick": defaultdict(deque),
            "ban": defaultdict(deque),
        }

    def _threshold(self, s, kind: str):
        return s[f"anti_nuke_{kind}_count"]

    async def _record(self, guild: discord.Guild, actor: discord.abc.User,
                      kind: str, detail: str):
        if self.bot.user and actor.id == self.bot.user.id:
            return
        if is_whitelisted(guild.id, actor.id):
            return

        s = get_settings(guild.id)
        limit = self._threshold(s, kind)
        if not limit or limit < 1:
            return

        window = s["anti_nuke_window"] or DEFAULT_NUKE_WINDOW
        now = time.monotonic()
        q = self.history[kind][(guild.id, actor.id)]
        q.append(now)
        while q and now - q[0] > window:
            q.popleft()
        if len(q) < limit:
            return

        member = guild.get_member(actor.id)
        punish = (s["anti_nuke_punish"] or "timeout").lower()
        result = "unknown"
        note = ""
        if member is None:
            result = "left"
            note = "実行者はサーバーにいません。"
        elif not can_act_on(member):
            result = "skipped"
            note = "Botより上位、または処罰できない対象です。"
        else:
            result = await punishment(member, punish, f"Anti-Nuke ({kind})")

        q.clear()
        desc = (
            f"短時間の大量{detail}を検出しました。\n"
            f"実行者: {actor.mention} (`{actor.id}`)\n"
            f"件数: **{limit}+** / {window}秒\n"
            f"処置: **{result}**"
        )
        if note:
            desc += f"\n{note}"
        await report(guild, "Anti-Nuke", desc, member, discord.Color.red())

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        window = (get_settings(guild.id)["anti_nuke_window"]
                  or DEFAULT_NUKE_WINDOW)
        actor = await find_audit_user(
            guild, discord.AuditLogAction.channel_delete,
            target_id=channel.id, window=float(window) + 2,
        )
        if actor:
            await self._record(guild, actor, "channel",
                               f"チャンネル削除 (`{channel.name}`)")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        window = (get_settings(guild.id)["anti_nuke_window"]
                  or DEFAULT_NUKE_WINDOW)
        actor = await find_audit_user(
            guild, discord.AuditLogAction.kick,
            target_id=member.id, window=float(window) + 2,
        )
        if actor:
            await self._record(guild, actor, "kick",
                               f"Kick ({member} / `{member.id}`)")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        window = (get_settings(guild.id)["anti_nuke_window"]
                  or DEFAULT_NUKE_WINDOW)
        actor = await find_audit_user(
            guild, discord.AuditLogAction.ban,
            target_id=user.id, window=float(window) + 2,
        )
        if actor:
            await self._record(guild, actor, "ban",
                               f"Ban ({user} / `{user.id}`)")

    @app_commands.command(name="anti-nuke", description="Anti-Nukeを設定")
    @app_commands.describe(
        channel="何件のチャンネル削除で発動するか",
        kick="何件のKickで発動するか",
        ban="何件のBanで発動するか",
        window="判定秒数",
        punish="timeout / kick / ban / none",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def anti_nuke(self, interaction: discord.Interaction,
                        channel: int | None = None,
                        kick: int | None = None,
                        ban: int | None = None,
                        window: int | None = None,
                        punish: str | None = None):
        values = {}
        for name, val in (
            ("anti_nuke_channel_count", channel),
            ("anti_nuke_kick_count", kick),
            ("anti_nuke_ban_count", ban),
        ):
            if val is not None:
                if val < 1:
                    return await respond(interaction,
                                         content="件数は1以上です。",
                                         ephemeral=True)
                values[name] = val
        if window is not None:
            if window < 3 or window > 120:
                return await respond(interaction,
                                     content="windowは3〜120秒です。",
                                     ephemeral=True)
            values["anti_nuke_window"] = window
        if punish is not None:
            if punish.lower() not in VALID_NUKE_PUNISH:
                return await respond(
                    interaction,
                    content=f"punishは {', '.join(sorted(VALID_NUKE_PUNISH))} です。",
                    ephemeral=True,
                )
            values["anti_nuke_punish"] = punish.lower()
        if values:
            update_settings(interaction.guild_id, **values)
        s = get_settings(interaction.guild_id)
        await respond(interaction, embed=make_embed(
            "Anti-Nuke",
            f"channel: `{s['anti_nuke_channel_count']}`\n"
            f"kick: `{s['anti_nuke_kick_count']}`\n"
            f"ban: `{s['anti_nuke_ban_count']}`\n"
            f"window: `{s['anti_nuke_window'] or DEFAULT_NUKE_WINDOW}`秒\n"
            f"punish: `{s['anti_nuke_punish'] or 'timeout'}`\n"
            "デフォルトの処罰はtimeoutです。noneにすると検知だけします。"
        ), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(NukeCog(bot))
