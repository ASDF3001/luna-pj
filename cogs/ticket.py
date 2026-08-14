import time

import discord
from discord import app_commands
from discord.ext import commands

from db import db, get_settings, update_settings, TICKET_COOLDOWN_SEC
from utils import respond, make_embed


class TicketView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="チケットを作成", style=discord.ButtonStyle.primary,
                       custom_id="ticket_create")
    async def create_ticket(self, interaction: discord.Interaction,
                            button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(content="サーバー内で使用してください。",
                                            ephemeral=True)
            return

        row = db.execute(
            "SELECT created_at FROM ticket_cooldowns WHERE guild_id=? AND user_id=?",
            (guild.id, interaction.user.id),
        ).fetchone()
        if row and time.time() - row["created_at"] < TICKET_COOLDOWN_SEC:
            remaining = int(TICKET_COOLDOWN_SEC - (time.time() - row["created_at"]))
            await interaction.followup.send(
                content=f"連続作成はできません。{remaining}秒後に再試行してください。",
                ephemeral=True)
            return

        s = get_settings(guild.id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True),
        }
        if s["ticket_role_id"]:
            role = guild.get_role(s["ticket_role_id"])
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
        try:
            channel = await guild.create_text_channel(
                f"ticket-{interaction.user.name}".lower()[:90],
                overwrites=overwrites,
                reason="Ticket created",
            )
            mention = ""
            if s["ticket_role_id"]:
                role = guild.get_role(s["ticket_role_id"])
                if role:
                    mention = role.mention + " "
            await channel.send(
                content=f"{mention}{interaction.user.mention}",
                embed=make_embed(s["ticket_title"] or "チケット",
                                 s["ticket_body"] or "お問い合わせありがとうございます。",
                                 discord.Color.blurple())
            )
            with db:
                db.execute(
                    "INSERT OR REPLACE INTO ticket_cooldowns VALUES (?, ?, ?)",
                    (guild.id, interaction.user.id, time.time()),
                )
            await interaction.followup.send(
                content=f"チケットを作成しました: {channel.mention}",
                ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(
                content=f"作成できませんでした: {e}",
                ephemeral=True)


class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(TicketView(bot, 0))

    @app_commands.command(name="ticket_setup", description="チケットパネルを設置")
    @app_commands.describe(title="チケットEmbedタイトル", body="本文",
                           role="作成時にメンションするロール",
                           open_text="チケット作成時のメッセージ")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_setup(self, interaction: discord.Interaction, title: str,
                           body: str, role: discord.Role | None = None,
                           open_text: str = "チケットを作成しました。"):
        update_settings(interaction.guild_id, ticket_channel_id=interaction.channel_id,
                        ticket_role_id=role.id if role else None,
                        ticket_title=title, ticket_body=body,
                        ticket_open_text=open_text)
        await interaction.channel.send(
            embed=make_embed(title, body, discord.Color.blurple()),
            view=TicketView(self.bot, interaction.guild_id)
        )
        await respond(interaction, content="Ticketパネルを設置しました。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
