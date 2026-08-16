import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from db import PURGE_MAX
from utils import respond, make_embed, report


class ConfirmView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.value: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "この確認は実行者だけが操作できます。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="削除する", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction,
                      button: discord.ui.Button):
        self.value = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction,
                     button: discord.ui.Button):
        self.value = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class ModCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="purge", description="直近のメッセージを削除")
    @app_commands.describe(
        amount="削除する件数 (1〜100)",
        bots_only="Bot/Webhookの投稿だけ削除する",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: int,
                    bots_only: bool = False):
        if amount < 1 or amount > PURGE_MAX:
            return await respond(
                interaction,
                content=f"件数は 1〜{PURGE_MAX} で指定してください。",
                ephemeral=True,
            )
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await respond(interaction,
                                 content="テキストチャンネルで使ってください。",
                                 ephemeral=True)
        me = interaction.guild.me if interaction.guild else None
        if not me or not channel.permissions_for(me).manage_messages:
            return await respond(interaction,
                                 content="このチャンネルでメッセージを削除する権限がありません。",
                                 ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        def check(msg: discord.Message) -> bool:
            if msg.id == interaction.id:
                return False
            if bots_only:
                return bool(msg.author.bot or msg.webhook_id)
            return True

        try:
            deleted = await channel.purge(limit=amount, check=check, bulk=True)
        except discord.Forbidden:
            return await interaction.followup.send(
                content="権限不足で削除できませんでした。", ephemeral=True)
        except discord.HTTPException as e:
            return await interaction.followup.send(
                content=f"削除に失敗しました: {e}", ephemeral=True)

        await interaction.followup.send(
            content=f"{len(deleted)}件削除しました。", ephemeral=True)
        await report(
            interaction.guild,
            "メッセージ削除",
            f"{interaction.user.mention} が {channel.mention} で "
            f"{len(deleted)}件削除しました。"
            + (" (Bot/Webhookのみ)" if bots_only else ""),
            interaction.user,
        )

    @app_commands.command(name="channel_delete", description="チャンネルを削除")
    @app_commands.describe(channel="削除するチャンネル")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def channel_delete(self, interaction: discord.Interaction,
                             channel: discord.abc.GuildChannel):
        me = interaction.guild.me if interaction.guild else None
        if not me or not channel.permissions_for(me).manage_channels:
            return await respond(interaction,
                                 content="そのチャンネルを削除する権限がありません。",
                                 ephemeral=True)

        view = ConfirmView(interaction.user.id)
        await respond(
            interaction,
            embed=make_embed(
                "チャンネル削除の確認",
                f"{channel.mention} (`{channel.name}`) を削除します。\n"
                "取り消せません。30秒以内に選んでください。",
                discord.Color.red(),
            ),
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if view.value is not True:
            return await interaction.followup.send(
                content="キャンセルしました。", ephemeral=True)

        name = channel.name
        cid = channel.id
        try:
            await channel.delete(
                reason=f"channel_delete by {interaction.user} ({interaction.user.id})"
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                content="権限不足で削除できませんでした。", ephemeral=True)
        except discord.HTTPException as e:
            return await interaction.followup.send(
                content=f"削除に失敗しました: {e}", ephemeral=True)

        await interaction.followup.send(
            content=f"`{name}` を削除しました。", ephemeral=True)
        await report(
            interaction.guild,
            "チャンネル削除",
            f"{interaction.user.mention} が `{name}` (`{cid}`) を削除しました。",
            interaction.user,
            discord.Color.red(),
        )
        await asyncio.sleep(0)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModCog(bot))
