import os
import asyncio
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "claims.db")
GUILD_ID = os.getenv("GUILD_ID")  # Optional: speeds up slash-command registration for one server.
COMMAND_ROLE_ID = os.getenv("COMMAND_ROLE_ID")

if not COMMAND_ROLE_ID or not COMMAND_ROLE_ID.isdigit():
    raise RuntimeError("COMMAND_ROLE_ID must be set to the numeric Discord role ID allowed to run commands.")

COMMAND_ROLE_ID = int(COMMAND_ROLE_ID)

db_lock = asyncio.Lock()

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = db_connect()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL UNIQUE,
        name TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS claim_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        claim_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        claimed_by INTEGER,
        claimed_at TEXT,
        UNIQUE(claim_id, name),
        FOREIGN KEY(claim_id) REFERENCES claims(id) ON DELETE CASCADE
    );
    """)
    conn.commit()
    conn.close()

def get_claim_by_id(claim_id):
    conn = db_connect()
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    conn.close()
    return row

def get_claim_by_name(guild_id, name):
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM claims WHERE guild_id = ? AND name = ?",
        (guild_id, name)
    ).fetchone()
    conn.close()
    return row

def get_options(claim_id):
    conn = db_connect()
    rows = conn.execute(
        "SELECT * FROM claim_options WHERE claim_id = ? ORDER BY id",
        (claim_id,)
    ).fetchall()
    conn.close()
    return rows

def claim_option_atomic(option_id, user_id):
    """
    The UPDATE is the important part: only the first transaction that changes
    claimed_by from NULL can win the option.
    """
    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM claim_options WHERE id = ?",
            (option_id,)
        ).fetchone()

        if row is None:
            conn.rollback()
            return None, "missing"

        if row["claimed_by"] is not None:
            conn.rollback()
            return row, "claimed"

        existing_claim = conn.execute(
            """
            SELECT * FROM claim_options
            WHERE claim_id = ? AND claimed_by = ?
            """,
            (row["claim_id"], user_id)
        ).fetchone()

        if existing_claim is not None:
            conn.rollback()
            return existing_claim, "already_claimed"

        now = datetime.now(timezone.utc).isoformat()
        changed = conn.execute(
            """
            UPDATE claim_options
            SET claimed_by = ?, claimed_at = ?
            WHERE id = ? AND claimed_by IS NULL
            """,
            (user_id, now, option_id)
        ).rowcount

        if changed != 1:
            conn.rollback()
            row = conn.execute(
                "SELECT * FROM claim_options WHERE id = ?",
                (option_id,)
            ).fetchone()
            return row, "claimed"

        row = conn.execute(
            "SELECT * FROM claim_options WHERE id = ?",
            (option_id,)
        ).fetchone()
        conn.commit()
        return row, "won"
    finally:
        conn.close()

def build_embed(claim_id):
    claim = get_claim_by_id(claim_id)
    options = get_options(claim_id)

    claimed = sum(1 for o in options if o["claimed_by"] is not None)

    embed = discord.Embed(
        title=f"📋 {claim['name']}",
        description=claim["message"],
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Progress",
        value=f"**{claimed} / {len(options)} claimed**",
        inline=False
    )

    lines = []
    for option in options:
        if option["claimed_by"] is None:
            lines.append(f"🟢 **{option['name']}** — Available")
        else:
            lines.append(f"🔒 **{option['name']}** — <@{option['claimed_by']}>")

    embed.add_field(
        name="Options",
        value="\n".join(lines),
        inline=False
    )
    return embed

class ClaimButton(discord.ui.Button):
    def __init__(self, option_id, label, disabled=False):
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.primary,
            custom_id=f"claim_option:{option_id}",
            disabled=disabled
        )
        self.option_id = option_id

    async def callback(self, interaction: discord.Interaction):
        async with db_lock:
            option, result = claim_option_atomic(
                self.option_id,
                interaction.user.id
            )

        if result == "missing":
            await interaction.response.send_message(
                "That claim option no longer exists.",
                ephemeral=True
            )
            return

        if result == "claimed":
            await interaction.response.send_message(
                f"Sorry — **{option['name']}** was already claimed.",
                ephemeral=True
            )
            return

        if result == "already_claimed":
            await interaction.response.send_message(
                f"You already claimed **{option['name']}** on this board. You can only claim one option.",
                ephemeral=True
            )
            return

        claim = get_claim_by_id(option["claim_id"])
        options = get_options(claim["id"])

        view = ClaimView.from_database(claim["id"])

        await interaction.response.edit_message(
            embed=build_embed(claim["id"]),
            view=view
        )

        await interaction.followup.send(
            f"✅ You claimed **{option['name']}**!",
            ephemeral=True
        )

class ClaimView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=None)
        for option in options:
            self.add_item(
                ClaimButton(
                    option["id"],
                    option["name"],
                    disabled=option["claimed_by"] is not None
                )
            )

    @classmethod
    def from_database(cls, claim_id):
        return cls(get_options(claim_id))

class ClaimBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        init_db()

        # Re-register persistent buttons for existing claim messages.
        conn = db_connect()
        claims = conn.execute("SELECT id FROM claims").fetchall()
        conn.close()

        for claim in claims:
            self.add_view(ClaimView.from_database(claim["id"]))

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Synced commands to guild {GUILD_ID}")
        else:
            await self.tree.sync()
            print("Synced global commands.")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")

bot = ClaimBot()

class ClaimGroup(app_commands.Group):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False

        member = interaction.user
        return isinstance(member, discord.Member) and any(
            role.id == COMMAND_ROLE_ID for role in member.roles
        )

claim_group = ClaimGroup(
    name="claim",
    description="Create and manage first-come, first-served claim boards."
)

@claim_group.command(
    name="create",
    description="Create a new claim board."
)
@app_commands.describe(
    name="Name of the claim board, such as test-1.",
    message="Message/instructions displayed above the options.",
    options="Comma-separated options, e.g. bug tester, GUI tester, graphics tester."
)
async def claim_create(
    interaction: discord.Interaction,
    name: str,
    message: str,
    options: str
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True
        )
        return

    parsed = [x.strip() for x in options.split(",") if x.strip()]

    # Discord buttons can be displayed in rows of up to 5. This bot supports
    # up to 25 options, which fits Discord's component row limits.
    if not parsed:
        await interaction.response.send_message(
            "Please provide at least one option.",
            ephemeral=True
        )
        return

    if len(parsed) > 25:
        await interaction.response.send_message(
            "Please provide 25 or fewer options.",
            ephemeral=True
        )
        return

    if len(set(x.casefold() for x in parsed)) != len(parsed):
        await interaction.response.send_message(
            "Option names must be unique.",
            ephemeral=True
        )
        return

    if get_claim_by_name(interaction.guild.id, name):
        await interaction.response.send_message(
            f"A claim board named **{name}** already exists in this server.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    # Insert the claim first so the message ID can be saved afterward.
    conn = db_connect()
    cursor = conn.execute(
        """
        INSERT INTO claims
        (guild_id, channel_id, message_id, name, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            interaction.guild.id,
            interaction.channel.id,
            0,
            name,
            message,
            datetime.now(timezone.utc).isoformat()
        )
    )
    claim_id = cursor.lastrowid

    for option_name in parsed:
        conn.execute(
            "INSERT INTO claim_options (claim_id, name) VALUES (?, ?)",
            (claim_id, option_name)
        )

    conn.commit()
    conn.close()

    view = ClaimView.from_database(claim_id)

    try:
        sent = await interaction.channel.send(
            embed=build_embed(claim_id),
            view=view
        )

        conn = db_connect()
        conn.execute(
            "UPDATE claims SET message_id = ? WHERE id = ?",
            (sent.id, claim_id)
        )
        conn.commit()
        conn.close()

        # Persistent view registration for this running process.
        bot.add_view(view, message_id=sent.id)

        await interaction.followup.send(
            f"✅ Created **{name}** with {len(parsed)} options.\n"
            f"{sent.jump_url}",
            ephemeral=True
        )

    except Exception:
        conn = db_connect()
        conn.execute("DELETE FROM claims WHERE id = ?", (claim_id,))
        conn.commit()
        conn.close()
        raise

@claim_group.command(
    name="show-results",
    description="Show who claimed each option."
)
@app_commands.describe(
    name="Name of the claim board, such as test-1."
)
async def claim_show_results(
    interaction: discord.Interaction,
    name: str
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True
        )
        return

    claim = get_claim_by_name(interaction.guild.id, name)

    if not claim:
        await interaction.response.send_message(
            f"I couldn't find a claim board named **{name}**.",
            ephemeral=True
        )
        return

    options = get_options(claim["id"])
    claimed = [o for o in options if o["claimed_by"] is not None]
    unclaimed = [o for o in options if o["claimed_by"] is None]

    embed = discord.Embed(
        title=f"📊 Results — {claim['name']}",
        color=discord.Color.green() if not unclaimed else discord.Color.blurple()
    )

    if claimed:
        embed.add_field(
            name=f"🔒 Claimed ({len(claimed)})",
            value="\n".join(
                f"**{o['name']}** — <@{o['claimed_by']}>"
                for o in claimed
            ),
            inline=False
        )
    else:
        embed.add_field(
            name="🔒 Claimed",
            value="Nothing has been claimed yet.",
            inline=False
        )

    if unclaimed:
        embed.add_field(
            name=f"🟢 Unclaimed ({len(unclaimed)})",
            value="\n".join(f"**{o['name']}**" for o in unclaimed),
            inline=False
        )
    else:
        embed.add_field(
            name="🟢 Unclaimed",
            value="All options have been claimed! 🎉",
            inline=False
        )

    embed.set_footer(text=f"{len(claimed)} / {len(options)} claimed")

    await interaction.response.send_message(embed=embed, ephemeral=True)

@claim_group.command(
    name="reset",
    description="Reset all options on a claim board."
)
@app_commands.describe(
    name="Name of the claim board to reset."
)
async def claim_reset(interaction: discord.Interaction, name: str):
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True
        )
        return

    claim = get_claim_by_name(interaction.guild.id, name)
    if not claim:
        await interaction.response.send_message(
            f"I couldn't find **{name}**.",
            ephemeral=True
        )
        return

    conn = db_connect()
    conn.execute(
        "UPDATE claim_options SET claimed_by = NULL, claimed_at = NULL WHERE claim_id = ?",
        (claim["id"],)
    )
    conn.commit()
    conn.close()

    try:
        channel = bot.get_channel(claim["channel_id"])
        if channel:
            message = await channel.fetch_message(claim["message_id"])
            await message.edit(
                embed=build_embed(claim["id"]),
                view=ClaimView.from_database(claim["id"])
            )
    except discord.HTTPException:
        pass

    await interaction.response.send_message(
        f"🔄 **{name}** has been reset.",
        ephemeral=True
    )

@claim_group.command(
    name="delete",
    description="Delete a claim board."
)
@app_commands.describe(
    name="Name of the claim board to delete."
)
async def claim_delete(interaction: discord.Interaction, name: str):
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True
        )
        return

    claim = get_claim_by_name(interaction.guild.id, name)
    if not claim:
        await interaction.response.send_message(
            f"I couldn't find **{name}**.",
            ephemeral=True
        )
        return

    conn = db_connect()
    conn.execute("DELETE FROM claims WHERE id = ?", (claim["id"],))
    conn.commit()
    conn.close()

    try:
        channel = bot.get_channel(claim["channel_id"])
        if channel:
            message = await channel.fetch_message(claim["message_id"])
            await message.delete()
    except discord.HTTPException:
        pass

    await interaction.response.send_message(
        f"🗑️ Deleted **{name}**.",
        ephemeral=True
    )

bot.tree.add_command(claim_group)

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.CheckFailure):
        message = "You need the required role to use this command."
    else:
        print(f"Command error: {error}")
        message = "Something went wrong while running that command."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set.")

bot.run(token)
