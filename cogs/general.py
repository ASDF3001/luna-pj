import discord
from discord import app_commands
from discord.ext import commands

from db import update_settings
from utils import respond, make_embed, color_from_hex


class GeneralCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="embed", description="Embedを送信")
    @app_commands.describe(title="タイトル", body="本文", footer="フッター", color="HEX色")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def embed_command(self, interaction: discord.Interaction, title: str,
                            body: str, footer: str | None = None,
                            color: str = "#5865F2"):
        try:
            c = color_from_hex(color)
        except ValueError as e:
            return await respond(interaction, content=str(e), ephemeral=True)
        await interaction.channel.send(embed=make_embed(title, body, c, footer))
        await respond(interaction, content="Embedを送信しました。", ephemeral=True)

    @app_commands.command(name="hex", description="HEXカラーを確認")
    @app_commands.describe(color="例: #5865F2")
    async def hex_command(self, interaction: discord.Interaction, color: str):
        try:
            c = color_from_hex(color)
            value = color.strip().replace("#", "").upper()
            r, g, b = c.to_rgb()
            await respond(interaction, embed=make_embed(
                "HEX Color", f"`#{value}`\nRGB: `{r}, {g}, {b}`",
                c), ephemeral=True)
        except ValueError as e:
            await respond(interaction, content=str(e), ephemeral=True)

    @app_commands.command(name="trap", description="トラップ/ログチャンネルを設定")
    @app_commands.describe(channel="警告・処置報告を送るチャンネル")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trap(self, interaction: discord.Interaction,
                   channel: discord.TextChannel | None = None):
        update_settings(interaction.guild_id,
                        trap_channel_id=channel.id if channel else None)
        label = channel.mention if channel else "OFF"
        await respond(interaction, content=f"Trap channel: {label}", ephemeral=True)

    @app_commands.command(name="top", description="チャンネルで最初のメッセージを探す")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def top(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await respond(interaction,
                                 content="テキストチャンネルで使用してください。",
                                 ephemeral=True)
        await respond(interaction, content="最初のメッセージを検索中...", ephemeral=True)
        try:
            first = None
            async for msg in channel.history(limit=1, oldest_first=True):
                first = msg
            if not first:
                return await interaction.edit_original_response(
                    content="メッセージがありません。")
            await interaction.edit_original_response(
                content=f"**最初に喋った人**\n"
                        f"ユーザー: {first.author.mention} (`{first.author.id}`)\n"
                        f"メッセージ: {first.jump_url}\n"
                        f"日時: <t:{int(first.created_at.timestamp())}:F>"
            )
        except discord.Forbidden:
            await interaction.edit_original_response(
                content="履歴を読む権限がありません。")
        except discord.HTTPException as e:
            await interaction.edit_original_response(content=f"エラー: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
