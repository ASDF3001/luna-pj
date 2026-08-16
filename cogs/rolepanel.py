import discord
from discord import app_commands
from discord.ext import commands

from db import db
from utils import respond, make_embed, role_is_dangerous


def _load_roles(message_id: int) -> list[tuple[int, str]]:
    rows = db.execute(
        "SELECT role_id, label FROM role_panel_roles WHERE message_id=? ORDER BY rowid",
        (message_id,),
    ).fetchall()
    return [(r["role_id"], r["label"] or "") for r in rows]


def _save_panel(message_id: int, guild_id: int, channel_id: int,
                title: str, body: str, roles: list[tuple[int, str]]):
    with db:
        db.execute(
            """INSERT OR REPLACE INTO role_panels
               (message_id, guild_id, channel_id, mode, title, body)
               VALUES (?, ?, ?, 'auto', ?, ?)""",
            (message_id, guild_id, channel_id, title, body),
        )
        db.execute("DELETE FROM role_panel_roles WHERE message_id=?", (message_id,))
        db.executemany(
            "INSERT INTO role_panel_roles VALUES (?, ?, ?)",
            [(message_id, rid, label) for rid, label in roles],
        )


def _parse_roles(guild: discord.Guild, raw: str) -> list[discord.Role] | str:
    found: list[discord.Role] = []
    for part in raw.replace("、", ",").split(","):
        token = part.strip()
        if not token:
            continue
        role = None
        if token.startswith("<@&") and token.endswith(">"):
            try:
                role = guild.get_role(int(token[3:-1]))
            except ValueError:
                role = None
        elif token.isdigit():
            role = guild.get_role(int(token))
        else:
            role = discord.utils.get(guild.roles, name=token)
        if not role:
            return f"ロールが見つかりません: `{token}`"
        if role.is_default() or role.managed:
            return f"{role.mention} は扱えません。"
        if role_is_dangerous(role):
            return f"{role.mention} は危険な権限を持っているのでパネルに置けません。"
        found.append(role)
    return found


async def _toggle_role(interaction: discord.Interaction, role: discord.Role) -> str:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return "サーバー内で使ってください。"
    me = interaction.guild.me if interaction.guild else None
    if not me or not me.guild_permissions.manage_roles:
        return "Botにロール管理権限がありません。"
    if role >= me.top_role:
        return f"{role.mention} はBotより上なので付けられません。"
    if role.is_default() or role.managed:
        return "そのロールは扱えません。"
    try:
        if role in member.roles:
            await member.remove_roles(role, reason="ロールパネル")
            return f"{role.mention} を外しました。"
        await member.add_roles(role, reason="ロールパネル")
        return f"{role.mention} を付けました。"
    except discord.Forbidden:
        return "権限不足でロールを変更できませんでした。"
    except discord.HTTPException as e:
        return f"失敗しました: {e}"


def build_panel_view(message_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    roles = _load_roles(message_id)
    if not roles:
        return view
    if len(roles) <= 5:
        for rid, label in roles:
            view.add_item(discord.ui.Button(
                label=(label or "ロール")[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"rp:{message_id}:{rid}",
            ))
    else:
        options = [
            discord.SelectOption(label=(label or str(rid))[:100], value=str(rid))
            for rid, label in roles[:25]
        ]
        view.add_item(discord.ui.Select(
            placeholder="ロールを選ぶ",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"rpselect:{message_id}",
        ))
    return view


class RolePanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        rows = db.execute("SELECT message_id FROM role_panels").fetchall()
        for row in rows:
            self.bot.add_view(build_panel_view(row["message_id"]))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type is not discord.InteractionType.component:
            return
        if interaction.response.is_done():
            return
        cid = (interaction.data or {}).get("custom_id") or ""
        rid = None
        if cid.startswith("rp:"):
            parts = cid.split(":")
            if len(parts) != 3:
                return
            try:
                rid = int(parts[2])
            except ValueError:
                return
        elif cid.startswith("rpselect:"):
            values = (interaction.data or {}).get("values") or []
            if not values:
                return
            try:
                rid = int(values[0])
            except ValueError:
                return
        else:
            return

        role = interaction.guild.get_role(rid) if interaction.guild else None
        if not role:
            await interaction.response.send_message(
                "ロールが見つかりません。", ephemeral=True)
            return
        msg = await _toggle_role(interaction, role)
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="rolepanel", description="ロールパネルを設置")
    @app_commands.describe(
        roles="付けるロール。カンマ区切りで複数指定 (メンション・ID・名前)",
        title="パネルのタイトル",
        body="パネルの説明",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def rolepanel(self, interaction: discord.Interaction, roles: str,
                        title: str = "ロールパネル",
                        body: str = "ボタンまたはメニューからロールを付け外しできます。"):
        if not interaction.guild:
            return await respond(interaction, content="サーバー内で使ってください。",
                                 ephemeral=True)
        parsed = _parse_roles(interaction.guild, roles)
        if isinstance(parsed, str):
            return await respond(interaction, content=parsed, ephemeral=True)
        if not parsed:
            return await respond(interaction, content="ロールを1つ以上指定してください。",
                                 ephemeral=True)
        if len(parsed) > 25:
            return await respond(interaction, content="ロールは25個までです。",
                                 ephemeral=True)

        pairs = [(r.id, r.name) for r in parsed]
        embed = make_embed(title, body, discord.Color.blurple())
        embed.add_field(
            name="対象ロール",
            value=", ".join(r.mention for r in parsed),
            inline=False,
        )
        await interaction.response.defer(ephemeral=True)
        sent = await interaction.channel.send(embed=embed)
        _save_panel(sent.id, interaction.guild_id, interaction.channel_id,
                    title, body, pairs)
        view = build_panel_view(sent.id)
        await sent.edit(view=view)
        self.bot.add_view(view)
        await interaction.followup.send("ロールパネルを設置しました。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RolePanelCog(bot))
