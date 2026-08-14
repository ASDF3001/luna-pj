import os
import re
import sqlite3
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment variable is not set.")

DB_PATH = os.getenv("ANTI_BOT_DB", "anti_bot.sqlite3")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.reactions = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

with db:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id INTEGER PRIMARY KEY,
        anti_raid_count INTEGER,
        anti_raid_punish TEXT,
        anti_spam_count INTEGER,
        anti_spam_punish TEXT,
        anti_url_count INTEGER,
        anti_url_treatment TEXT,
        auth_channel_id INTEGER,
        auth_role_id INTEGER,
        auth_mode TEXT,
        auth_title TEXT,
        auth_body TEXT,
        auth_button_label TEXT,
        ticket_channel_id INTEGER,
        ticket_role_id INTEGER,
        ticket_title TEXT,
        ticket_body TEXT,
        ticket_open_text TEXT,
        trap_channel_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS whitelist (
        guild_id INTEGER,
        user_id INTEGER,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS blacklist (
        guild_id INTEGER,
        user_id INTEGER,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS auth_codes (
        guild_id INTEGER,
        user_id INTEGER,
        code TEXT,
        expires REAL,
        PRIMARY KEY (guild_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS auth_attempts (
        user_id INTEGER PRIMARY KEY,
        count INTEGER DEFAULT 0,
        first_attempt REAL
    );

    CREATE TABLE IF NOT EXISTS ticket_cooldowns (
        guild_id INTEGER,
        user_id INTEGER,
        created_at REAL,
        PRIMARY KEY (guild_id, user_id)
    );
    """)

VALID_PUNISH = {"kick", "ban", "timeout"}
VALID_TREATMENT = {"delete", "kick", "ban", "timeout"}


def ensure_guild(guild_id: int):
    with db:
        db.execute(
            """INSERT OR IGNORE INTO guild_settings
            (guild_id, anti_raid_count, anti_raid_punish,
             anti_spam_count, anti_spam_punish,
             anti_url_count, anti_url_treatment,
             auth_mode, auth_title, auth_body, auth_button_label,
             ticket_title, ticket_body, ticket_open_text)
             VALUES (?, NULL, 'timeout', NULL, 'timeout', NULL, 'delete',
                     'button', '認証', 'ボタンを押して認証してください。',
                     '認証する', 'チケット作成', '下のボタンからチケットを作成できます。',
                     'チケットを作成しました。')""",
            (guild_id,),
        )


def get_settings(guild_id: int):
    ensure_guild(guild_id)
    return db.execute(
        "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
    ).fetchone()


def update_settings(guild_id: int, **values):
    ensure_guild(guild_id)
    if not values:
        return
    columns = ", ".join(f"{k} = ?" for k in values)
    params = list(values.values()) + [guild_id]
    with db:
        db.execute(f"UPDATE guild_settings SET {columns} WHERE guild_id = ?", params)


def is_whitelisted(guild_id: int, user_id: int) -> bool:
    return db.execute(
        "SELECT 1 FROM whitelist WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone() is not None


def is_blacklisted(guild_id: int, user_id: int) -> bool:
    return db.execute(
        "SELECT 1 FROM blacklist WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone() is not None


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


async def punishment(member: discord.Member, action: str, reason: str) -> str:
    action = (action or "delete").lower()

    # Bot自身は処罰しない
    if member.id == member.guild.me.id:
        return "skipped"

    guild = member.guild
    perms = guild.me.guild_permissions

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


# --- レート制限用のインメモリ履歴 ---

join_history: dict[int, deque] = defaultdict(deque)
spam_history: dict[tuple, dict[int, deque]] = defaultdict(lambda: defaultdict(deque))
url_history: dict[tuple, dict[int, deque]] = defaultdict(lambda: defaultdict(deque))

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


async def handle_anti(message: discord.Message):
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
        q = spam_history[key][message.channel.id]
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
        q = url_history[key][message.channel.id]
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


@bot.event
async def on_member_join(member: discord.Member):
    if is_whitelisted(member.guild.id, member.id):
        return
    s = get_settings(member.guild.id)
    count = s["anti_raid_count"]
    if not count or count <= 0:
        return

    now = time.monotonic()
    q = join_history[member.guild.id]
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


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None or (bot.user and payload.user_id == bot.user.id):
        return
    s = get_settings(payload.guild_id)
    if s["auth_mode"] != "reaction":
        return
    if s["auth_channel_id"] != payload.channel_id:
        return
    if str(payload.emoji) != "✅":
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if member:
        await start_auth(member, guild)


# --- 認証 ---

AUTH_MAX_ATTEMPTS = 5
AUTH_LOCKOUT_SEC = 600


def check_auth_attempts(user_id: int) -> bool:
    """試行回数が上限に達していたらTrue"""
    row = db.execute(
        "SELECT count, first_attempt FROM auth_attempts WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if not row:
        return False
    if time.time() - row["first_attempt"] > AUTH_LOCKOUT_SEC:
        # ロックアウト期間を過ぎたのでリセット
        with db:
            db.execute("DELETE FROM auth_attempts WHERE user_id=?", (user_id,))
        return False
    return row["count"] >= AUTH_MAX_ATTEMPTS


def record_auth_attempt(user_id: int):
    row = db.execute(
        "SELECT count, first_attempt FROM auth_attempts WHERE user_id=?",
        (user_id,),
    ).fetchone()
    now = time.time()
    if not row or now - row["first_attempt"] > AUTH_LOCKOUT_SEC:
        with db:
            db.execute(
                "INSERT OR REPLACE INTO auth_attempts VALUES (?, 1, ?)",
                (user_id, now),
            )
    else:
        with db:
            db.execute(
                "UPDATE auth_attempts SET count = count + 1 WHERE user_id=?",
                (user_id,),
            )


async def start_auth(member: discord.Member, guild: discord.Guild,
                     channel_id: int | None = None, reaction: bool = False) -> bool:
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


async def verify_dm(member: discord.User, content: str) -> bool:
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

    guild = bot.get_guild(row["guild_id"])
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
        db.execute("DELETE FROM auth_attempts WHERE user_id=?", (member.id,))
    return True


# --- イベント ---

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user} | synced {len(synced)} commands")
    except Exception as e:
        print(f"Slash command sync error: {e}")


@bot.event
async def on_message(message: discord.Message):
    # DM認証
    if message.guild is None and not message.author.bot:
        if await verify_dm(message.author, message.content):
            await message.channel.send("認証に成功しました。サーバーをご確認ください。")
            return

    await handle_anti(message)
    await bot.process_commands(message)


# --- View ---

class AuthView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="認証する", style=discord.ButtonStyle.success,
                       custom_id="auth_button")
    async def auth_button(self, interaction: discord.Interaction,
                          button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = bot.get_guild(self.guild_id)
        if not guild:
            await interaction.followup.send(content="サーバーが見つかりません。",
                                            ephemeral=True)
            return
        ok = await start_auth(interaction.user, guild)
        msg = ("DMに認証コードを送りました。DMを確認してください。"
               if ok else "DMを送れませんでした。DMを開放してください。")
        await interaction.followup.send(content=msg, ephemeral=True)


TICKET_COOLDOWN_SEC = 300


class TicketView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
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

        # レート制限
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
            interaction.user: discord.PermissionOverwrite(view_channel=True,
                                                           send_messages=True,
                                                           read_message_history=True),
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


# --- スラッシュコマンド ---

@bot.tree.command(name="anti-raid", description="Anti-Raidを設定")
@app_commands.describe(count="何人の参加で発動するか", punish="kick / ban / timeout")
@app_commands.checks.has_permissions(manage_guild=True)
async def anti_raid(interaction: discord.Interaction, count: int | None = None,
                    punish: str | None = None):
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


@bot.tree.command(name="anti-spam", description="Anti-Spamを設定")
@app_commands.describe(count="指定回数の連投で発動", punish="kick / ban / timeout")
@app_commands.checks.has_permissions(manage_guild=True)
async def anti_spam(interaction: discord.Interaction, count: int | None = None,
                    punish: str | None = None):
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


@bot.tree.command(name="anti-url", description="Anti-URLを設定")
@app_commands.describe(count="指定回数のURL投稿で発動",
                       treatment="delete / kick / ban / timeout")
@app_commands.checks.has_permissions(manage_guild=True)
async def anti_url(interaction: discord.Interaction, count: int | None = None,
                   treatment: str | None = None):
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


@bot.tree.command(name="white_list", description="Anti対象外に追加")
@app_commands.describe(user="対象ユーザー")
@app_commands.checks.has_permissions(manage_guild=True)
async def white_list(interaction: discord.Interaction, user: discord.Member):
    with db:
        db.execute("INSERT OR IGNORE INTO whitelist VALUES (?, ?)",
                   (interaction.guild_id, user.id))
    await respond(interaction, content=f"{user.mention} をWhitelistに追加しました。",
                  ephemeral=True)


@bot.tree.command(name="black_list", description="認証拒否リストに追加")
@app_commands.describe(userid="ユーザーID")
@app_commands.checks.has_permissions(manage_guild=True)
async def black_list(interaction: discord.Interaction, userid: str):
    try:
        uid = int(userid)
    except ValueError:
        return await respond(interaction, content="useridが不正です。", ephemeral=True)
    with db:
        db.execute("INSERT OR IGNORE INTO blacklist VALUES (?, ?)",
                   (interaction.guild_id, uid))
    await respond(interaction, content=f"`{uid}` をBlacklistに追加しました。",
                  ephemeral=True)


@bot.tree.command(name="auth", description="認証パネルを設置")
@app_commands.describe(mode="button または reaction", role="認証後に付与するロール")
@app_commands.checks.has_permissions(manage_guild=True)
async def auth(interaction: discord.Interaction, mode: str = "button",
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
        await interaction.channel.send(embed=embed, view=AuthView(interaction.guild_id))
    else:
        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction("✅")
    await respond(interaction, content="認証パネルを設置しました。", ephemeral=True)


@bot.tree.command(name="embed", description="Embedを送信")
@app_commands.describe(title="タイトル", body="本文", footer="フッター", color="HEX色")
@app_commands.checks.has_permissions(manage_messages=True)
async def embed_command(interaction: discord.Interaction, title: str,
                        body: str, footer: str | None = None,
                        color: str = "#5865F2"):
    try:
        c = color_from_hex(color)
    except ValueError as e:
        return await respond(interaction, content=str(e), ephemeral=True)
    await interaction.channel.send(embed=make_embed(title, body, c, footer))
    await respond(interaction, content="Embedを送信しました。", ephemeral=True)


@bot.tree.command(name="ticket_setup", description="チケットパネルを設置")
@app_commands.describe(title="チケットEmbedタイトル", body="本文",
                       role="作成時にメンションするロール",
                       open_text="チケット作成時のメッセージ")
@app_commands.checks.has_permissions(manage_guild=True)
async def ticket_setup(interaction: discord.Interaction, title: str,
                       body: str, role: discord.Role | None = None,
                       open_text: str = "チケットを作成しました。"):
    update_settings(interaction.guild_id, ticket_channel_id=interaction.channel_id,
                    ticket_role_id=role.id if role else None,
                    ticket_title=title, ticket_body=body,
                    ticket_open_text=open_text)
    await interaction.channel.send(
        embed=make_embed(title, body, discord.Color.blurple()),
        view=TicketView(interaction.guild_id)
    )
    await respond(interaction, content="Ticketパネルを設置しました。", ephemeral=True)


@bot.tree.command(name="trap", description="トラップ/ログチャンネルを設定")
@app_commands.describe(channel="警告・処置報告を送るチャンネル")
@app_commands.checks.has_permissions(manage_guild=True)
async def trap(interaction: discord.Interaction,
               channel: discord.TextChannel | None = None):
    update_settings(interaction.guild_id,
                    trap_channel_id=channel.id if channel else None)
    label = channel.mention if channel else "OFF"
    await respond(interaction, content=f"Trap channel: {label}", ephemeral=True)


@bot.tree.command(name="hex", description="HEXカラーを確認")
@app_commands.describe(color="例: #5865F2")
async def hex_command(interaction: discord.Interaction, color: str):
    try:
        c = color_from_hex(color)
        value = color.strip().replace("#", "").upper()
        r, g, b = c.to_rgb()
        await respond(interaction, embed=make_embed(
            "HEX Color", f"`#{value}`\nRGB: `{r}, {g}, {b}`",
            c), ephemeral=True)
    except ValueError as e:
        await respond(interaction, content=str(e), ephemeral=True)


@bot.tree.command(name="anti", description="Anti設定を確認/解除")
@app_commands.describe(action="view / clear", target="raid / spam / url / all")
@app_commands.checks.has_permissions(manage_guild=True)
async def anti(interaction: discord.Interaction, action: str = "view",
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


@bot.tree.command(name="top", description="チャンネルで最初のメッセージを探す")
@app_commands.checks.has_permissions(manage_messages=True)
async def top(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await respond(interaction, content="テキストチャンネルで使用してください。",
                              ephemeral=True)
    await respond(interaction, content="最初のメッセージを検索中...", ephemeral=True)
    try:
        first = None
        async for msg in channel.history(limit=1, oldest_first=True):
            first = msg
        if not first:
            return await interaction.edit_original_response(content="メッセージがありません。")
        await interaction.edit_original_response(
            content=f"**最初に喋った人**\n"
                    f"ユーザー: {first.author.mention} (`{first.author.id}`)\n"
                    f"メッセージ: {first.jump_url}\n"
                    f"日時: <t:{int(first.created_at.timestamp())}:F>"
        )
    except discord.Forbidden:
        await interaction.edit_original_response(content="履歴を読む権限がありません。")
    except discord.HTTPException as e:
        await interaction.edit_original_response(content=f"エラー: {e}")


# --- グローバルエラーハンドラ ---

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


# Persistent views
bot.add_view(AuthView(0))
bot.add_view(TicketView(0))

bot.run(TOKEN)
