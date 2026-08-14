import os
import sys
from pathlib import Path

# bot/ 配下のモジュールをimportできるようにする
sys.path.insert(0, str(Path(__file__).parent))

import discord
from discord import app_commands
from discord.ext import commands

from cogs import EXTENSIONS
from utils import respond

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment variable is not set.")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.reactions = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user} | synced {len(synced)} commands")
    except Exception as e:
        print(f"Slash command sync error: {e}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction,
                               error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        missing = ", ".join(error.missing_permissions)
        await respond(interaction,
                      content=f"権限が不足しています: {missing}",
                      ephemeral=True)
    elif isinstance(error, app_commands.TransformerError):
        await respond(interaction,
                      content=f"引数の変換に失敗しました: {error}",
                      ephemeral=True)
    else:
        await respond(interaction,
                      content="コマンドの実行中にエラーが発生しました。",
                      ephemeral=True)
        raise error


async def main():
    async with bot:
        for ext in EXTENSIONS:
            await bot.load_extension(ext)
            print(f"Loaded: {ext}")
        await bot.start(TOKEN)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
