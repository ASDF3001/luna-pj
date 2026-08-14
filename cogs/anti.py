import re
import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from db import (
    db, get_settings, update_settings, is_whitelisted,
    VALID_PUNISH, VALID_TREATMENT,
)
from utils import respond, make_embed, punishment, report, send_warning

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


class AntiCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.join_history: dict[int, deque] = defaultdict(deque)
        self.spam_history = defaultdict(lambda: defaultdict(deque))
        self.url_history = defaultdict(lambda: defaultdict(deque))

    async def _handle_anti(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        guild = message.guild
        member = message.author
        if not isinstance(member, discord.Member):
            return
        if is_whitelisted(guild.id, member.id):
            return

        s = get_settings(guild.id)

        spam_count = s["anti_spam_count"]
        if spam_count and spam_count > 0:
            key = (guild.id, member.id)
            now = time.monotonic()
            q = self.spam_history[key][message.channel.id]
            q.append(now)
            while q and now - q[0] > 8:
                q.popleft()
            if len(q) >= spam_count:
                action = s["anti_spam_punish"]
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                result = await punishment(member, action, "Anti-Spam違反")
                await send_warning(
                    message.channel,
                    "Anti-Spam",
                    f"{member.mention} の連投を検出しました。\n処置: **{result}**",
                )
                await report(guild, "Anti-Spam 処置報告",
                             f"連投を検出し、**{result}** を実行しました。",
                             member)
                q.clear()
                return

        url_count = s["anti_url_count"]
        if url_count and url_count > 0 and URL_RE.search(message.content):
            key = (guild.id, member.id)
            now = time.monotonic()
            q = self.url_history[key][message.channel.id]
            q.append(now)
            while q and now - q[0] > 20:
                q.popleft()
            if len(q) >= url_count:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                treatment = s["anti_url_treatment"] or "delete"
                result = "delete"
                if treatment in ("kick", "ban", "timeout"):
                    result = await punishment(member, treatment, "Anti-URL違反")
                await send_warning(
                    message.channel,
                    "Anti-URL",
                    f"{member.mention} のURL投稿を検出しました。\n処置: **{result}**",
                )
                await report(guild, "Anti-URL 処置報告",
                             f"URL投稿を検出し、**{result}** を実行しました。",
                             member)
                q.clear()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self._handle_anti(message)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if is_whitelisted(member.guild.id, member.id):
            return
        s = get_settings(member.guild.id)
        count = s["anti_raid_count"]
        if not count or count <= 0:
            return

        now = time.monotonic()
        q = self.join_history[member.guild.id]
        q.append(now)
        while q and now - q[0] > 15:
            q.popleft()

        if len(q) >= count:
            action = s["anti_raid_punish"] or "kick"
            result = await punishment(member, action, "Anti-Raid発動")
            await report(member.guild, "Anti-Raid 処置報告",
                         f"短時間の大量参加を検出しました。\n処置: **{result}**",
                         member, discord.Color.red())
            q.clear()

    # --- コマンド ---

    @app_commands.command(name="anti-raid", description="Anti-Raidを設定")
    @app_commands.describe(count="何人の参加で発動するか", punish="kick / ban / timeout")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def anti_raid(self, interaction: discord.Interaction,
                        count: int | None = None, punish: str | None = None):
        if count is not None and count < 1:
            return await respond(interaction, content="countは1以上で指定してください。",
                                 ephemeral=True)
        if punish is not None and punish.lower() not in VALID_PUNISH:
            return await respond(interaction,
                                 content=f"punishは {', '.join(VALID_PUNISH)} のいずれかです。",
                                 ephemeral=True)
        update_settings(interaction.guild_id, anti_raid_count=count,
                        anti_raid_punish=punish.lower() if punish else None)
        await respond(interaction, embed=make_embed("Anti-Raid",
            f"count: `{count}`\npunish: `{punish}`"), ephemeral=True)

    @app_commands.command(name="anti-spam", description="Anti-Spamを設定")
    @app_commands.describe(count="指定回数の連投で発動", punish="kick / ban / timeout")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def anti_spam(self, interaction: discord.Interaction,
                        count: int | None = None, punish: str | None = None):
        if count is not None and count < 1:
            return await respond(interaction, content="countは1以上で指定してください。",
                                 ephemeral=True)
        if punish is not None and punish.lower() not in VALID_PUNISH:
            return await respond(interaction,
                                 content=f"punishは {', '.join(VALID_PUNISH)} のいずれかです。",
                                 ephemeral=True)
        update_settings(interaction.guild_id, anti_spam_count=count,
                        anti_spam_punish=punish.lower() if punish else None)
        await respond(interaction, embed=make_embed("Anti-Spam",
            f"count: `{count}`\npunish: `{punish}`"), ephemeral=True)

    @app_commands.command(name="anti-url", description="Anti-URLを設定")
    @app_commands.describe(count="指定回数のURL投稿で発動",
                           treatment="delete / kick / ban / timeout")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def anti_url(self, interaction: discord.Interaction,
                       count: int | None = None, treatment: str | None = None):
        if count is not None and count < 1:
            return await respond(interaction, content="countは1以上で指定してください。",
                                 ephemeral=True)
        if treatment is not None and treatment.lower() not in VALID_TREATMENT:
            return await respond(interaction,
                                 content=f"treatmentは {', '.join(VALID_TREATMENT)} のいずれかです。",
                                 ephemeral=True)
        update_settings(interaction.guild_id, anti_url_count=count,
                        anti_url_treatment=treatment.lower() if treatment else None)
        await respond(interaction, embed=make_embed("Anti-URL",
            f"count: `{count}`\ntreatment: `{treatment}`"), ephemeral=True)

    @app_commands.command(name="anti", description="Anti設定を確認/解除")
    @app_commands.describe(action="view / clear", target="raid / spam / url / all")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def anti(self, interaction: discord.Interaction, action: str = "view",
                   target: str = "all"):
        s = get_settings(interaction.guild_id)
        if action == "view":
            await respond(interaction, embed=make_embed(
                "Anti Status",
                f"Raid: `{s['anti_raid_count']}` / `{s['anti_raid_punish']}`\n"
                f"Spam: `{s['anti_spam_count']}` / `{s['anti_spam_punish']}`\n"
                f"URL: `{s['anti_url_count']}` / `{s['anti_url_treatment']}`"
            ), ephemeral=True)
            return

        if action != "clear":
            return await respond(interaction, content="actionは view / clear です。",
                                  ephemeral=True)

        values = {}
        if target in ("raid", "all"):
            values.update(anti_raid_count=None, anti_raid_punish="timeout")
        if target in ("spam", "all"):
            values.update(anti_spam_count=None, anti_spam_punish="timeout")
        if target in ("url", "all"):
            values.update(anti_url_count=None, anti_url_treatment="delete")
        if not values:
            return await respond(interaction, content="targetが不正です。",
                                  ephemeral=True)
        update_settings(interaction.guild_id, **values)
        await respond(interaction, content=f"Anti `{target}` を解除しました。",
                      ephemeral=True)

    @app_commands.command(name="white_list", description="Anti対象外に追加")
    @app_commands.describe(user="対象ユーザー")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def white_list(self, interaction: discord.Interaction,
                         user: discord.Member):
        with db:
            db.execute("INSERT OR IGNORE INTO whitelist VALUES (?, ?)",
                       (interaction.guild_id, user.id))
        await respond(interaction, content=f"{user.mention} をWhitelistに追加しました。",
                      ephemeral=True)

    @app_commands.command(name="black_list", description="認証拒否リストに追加")
    @app_commands.describe(userid="ユーザーID")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def black_list(self, interaction: discord.Interaction, userid: str):
        try:
            uid = int(userid)
        except ValueError:
            return await respond(interaction, content="useridが不正です。",
                                 ephemeral=True)
        with db:
            db.execute("INSERT OR IGNORE INTO blacklist VALUES (?, ?)",
                       (interaction.guild_id, uid))
        await respond(interaction, content=f"`{uid}` をBlacklistに追加しました。",
                      ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiCog(bot))
