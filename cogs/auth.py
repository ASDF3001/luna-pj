import secrets
import time

import discord
from discord import app_commands
from discord.ext import commands

from db import (
    db, get_settings, update_settings, is_blacklisted,
    check_auth_attempts, record_auth_attempt, clear_auth_attempts,
)
from utils import respond, make_embed


class AuthView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="認証する", style=discord.ButtonStyle.success,
                       custom_id="auth_button")
    async def auth_button(self, interaction: discord.Interaction,
                          button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            await interaction.followup.send(content="サーバーが見つかりません。",
                                            ephemeral=True)
            return
        cog: AuthCog | None = self.bot.get_cog("AuthCog")
        if not cog:
            return
        ok = await cog.start_auth(interaction.user, guild)
        msg = ("DMに認証コードを送りました。DMを確認してください。"
               if ok else "DMを送れませんでした。DMを開放してください。")
        await interaction.followup.send(content=msg, ephemeral=True)


class AuthCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(AuthView(bot, 0))

    async def start_auth(self, member: discord.Member, guild: discord.Guild,
                         channel_id: int | None = None,
                         reaction: bool = False) -> bool:
        if is_blacklisted(guild.id, member.id):
            try:
                await member.send("このサーバーでは認証できません。")
            except discord.HTTPException:
                pass
            return False

        code = f"{secrets.randbelow(1000000):06d}"
        with db:
            db.execute(
                "INSERT OR REPLACE INTO auth_codes VALUES (?, ?, ?, ?)",
                (guild.id, member.id, code, time.time() + 600),
            )
        try:
            await member.send(
                embed=make_embed(
                    "認証コード",
                    f"サーバー **{discord.utils.escape_markdown(guild.name)}** の認証コードです。\n\n"
                    f"```{code}```\nこのDMにコードだけ返信してください。",
                    discord.Color.blurple(),
                )
            )
            return True
        except discord.HTTPException:
            return False

    async def _verify_dm(self, member: discord.User, content: str) -> bool:
        code = content.strip()

        if check_auth_attempts(member.id):
            return False

        row = db.execute(
            "SELECT * FROM auth_codes WHERE user_id=? AND code=? AND expires>?",
            (member.id, code, time.time()),
        ).fetchone()
        if not row:
            record_auth_attempt(member.id)
            return False

        guild = self.bot.get_guild(row["guild_id"])
        if not guild:
            return False
        target = guild.get_member(member.id)
        if not target:
            return False

        if is_blacklisted(guild.id, member.id):
            return False

        s = get_settings(guild.id)
        if s["auth_role_id"]:
            role = guild.get_role(s["auth_role_id"])
            if role:
                try:
                    await target.add_roles(role, reason="DM認証完了")
                except discord.HTTPException:
                    pass

        with db:
            db.execute("DELETE FROM auth_codes WHERE guild_id=? AND user_id=?",
                       (guild.id, member.id))
        clear_auth_attempts(member.id)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None and not message.author.bot:
            if await self._verify_dm(message.author, message.content):
                await message.channel.send("認証に成功しました。サーバーをご確認ください。")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or (self.bot.user and payload.user_id == self.bot.user.id):
            return
        s = get_settings(payload.guild_id)
        if s["auth_mode"] != "reaction":
            return
        if s["auth_channel_id"] != payload.channel_id:
            return
        if str(payload.emoji) != "✅":
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if member:
            await self.start_auth(member, guild)

    @app_commands.command(name="auth", description="認証パネルを設置")
    @app_commands.describe(mode="button または reaction", role="認証後に付与するロール")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def auth(self, interaction: discord.Interaction, mode: str = "button",
                   role: discord.Role | None = None):
        mode = mode.lower()
        if mode not in ("button", "reaction"):
            return await respond(interaction, content="modeは button / reaction です。",
                                  ephemeral=True)
        update_settings(interaction.guild_id, auth_channel_id=interaction.channel_id,
                        auth_role_id=role.id if role else None, auth_mode=mode)
        s = get_settings(interaction.guild_id)
        embed = make_embed(s["auth_title"], s["auth_body"], discord.Color.blurple())
        if mode == "button":
            await interaction.channel.send(
                embed=embed, view=AuthView(self.bot, interaction.guild_id))
        else:
            msg = await interaction.channel.send(embed=embed)
            await msg.add_reaction("✅")
        await respond(interaction, content="認証パネルを設置しました。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AuthCog(bot))
