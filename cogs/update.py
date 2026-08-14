import os
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from utils import respond, make_embed

UPDATE_DIR = Path("bot/update")

def get_versions() -> list[str]:
    """updateディレクトリ内のファイルからバージョン一覧を取得して昇順にソートして返す"""
    if not UPDATE_DIR.exists():
        return []
    
    versions = []
    for file in UPDATE_DIR.glob("v*.txt"):
        # v1.0.txt -> v1.0
        versions.append(file.stem)
        
    def version_key(v):
        # "v1.01" -> 1.01 のように数値化してソート
        match = re.search(r'\d+(\.\d+)?', v)
        if match:
            return float(match.group())
        return 0.0
        
    return sorted(versions, key=version_key)

class UpdateCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="version", description="現在のBotのバージョンを表示します")
    async def version_command(self, interaction: discord.Interaction):
        versions = get_versions()
        if not versions:
            await respond(interaction, content="バージョン情報が見つかりません。")
            return
        
        latest_version = versions[-1]
        await respond(interaction, content=f"現在のバージョンは **{latest_version}** です。")

    @app_commands.command(name="update", description="アップデート内容を表示します")
    @app_commands.describe(version="確認したいバージョン (例: v1.0)。指定しない場合は最新を表示します")
    async def update_command(self, interaction: discord.Interaction, version: str | None = None):
        versions = get_versions()
        if not versions:
            await respond(interaction, content="アップデート情報が見つかりません。")
            return

        target_version = version
        if not target_version:
            target_version = versions[-1]
        else:
            # vが抜けていたら補完
            if not target_version.startswith("v"):
                target_version = f"v{target_version}"

        file_path = UPDATE_DIR / f"{target_version}.txt"
        if not file_path.exists():
            await respond(interaction, content=f"バージョン `{target_version}` のアップデート情報は見つかりませんでした。")
            return

        try:
            content = file_path.read_text(encoding="utf-8")
            
            embed = make_embed(
                title=f"Update Info: {target_version}",
                description=content,
                color=discord.Color.green()
            )
            await respond(interaction, embed=embed)
            
        except Exception as e:
            await respond(interaction, content=f"ファイルの読み込み中にエラーが発生しました: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(UpdateCog(bot))
