import asyncio
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

from database import (
    create_claim,
    delete_claim,
    get_all_claim_ids,
    get_claim_by_id,
    get_claim_by_name,
    get_claims_by_guild,
    get_interests,
    get_options,
    init_db,
    lock_claim,
    reset_claim,
    set_claim_message_id,
    toggle_interest,
)

GUILD_ID = os.getenv("GUILD_ID")
COMMAND_ROLE_ID = os.getenv("COMMAND_ROLE_ID")

if not COMMAND_ROLE_ID or not COMMAND_ROLE_ID.isdigit():
    raise RuntimeError("COMMAND_ROLE_ID must be set to the numeric Discord role ID allowed to run commands.")

COMMAND_ROLE_ID = int(COMMAND_ROLE_ID)
db_lock = asyncio.Lock()


def format_interested_members(user_ids):
    if not user_ids:
        return "No one has signed up yet."

    mentions = [f"<@{user_id}>" for user_id in user_ids]
    text = ", ".join(mentions)
    if len(text) <= 1024:
        return text

    visible = []
    for mention in mentions:
        candidate = ", ".join(visible + [mention])
        if len(candidate) > 980:
            break
        visible.append(mention)
    return f"{', '.join(visible)}\n…and {len(mentions) - len(visible)} more."


def build_embed(claim_id):
    claim = get_claim_by_id(claim_id)
    options = get_options(claim_id)
    interests = get_interests(claim_id)
    total_interested = sum(len(user_ids) for user_ids in interests.values())

    embed = discord.Embed(
        title=f"📋 {claim['name']}",
        description=claim["message"],
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Interest",
        value=f"**{total_interested}** sign-up(s) across **{len(options)}** option(s).",
        inline=False,
    )

    lines = []
    for option in options:
        user_ids = interests.get(option["id"], [])
        line = (
            f"**{option['name']}** ({len(user_ids)} interested): "
            f"{format_interested_members(user_ids)}"
        )
        if len("\n".join(lines + [line])) > 1024:
            notice = "…Use `/claim show-results` for the complete list."
            if len("\n".join(lines)) + 1 + len(notice) <= 1024:
                lines.append(notice)
            break
        lines.append(line)

    embed.add_field(name="Options", value="\n".join(lines), inline=False)
    if claim["locked"]:
        embed.set_footer(text="This claim is locked. Sign-ups are closed.")
    return embed


class ClaimButton(discord.ui.Button):
    def __init__(self, option_id, label, *, disabled=False):
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.primary,
            custom_id=f"claim_option:{option_id}",
            disabled=disabled,
        )
        self.option_id = option_id

    async def callback(self, interaction: discord.Interaction):
        async with db_lock:
            option, result = toggle_interest(self.option_id, interaction.user.id)

        if result == "missing":
            await interaction.response.send_message(
                "That option no longer exists.", ephemeral=True
            )
            return
        if result == "locked":
            await interaction.response.send_message(
                "This claim is locked. Sign-ups are closed.", ephemeral=True
            )
            return

        claim = get_claim_by_id(option["claim_id"])
        await interaction.response.edit_message(
            embed=build_embed(claim["id"]),
            view=ClaimView.from_database(claim["id"]),
        )

        message = (
            f"You signed up for **{option['name']}**!"
            if result == "interested"
            else f"You are no longer attending **{option['name']}**."
        )
        await interaction.followup.send(message, ephemeral=True)


class ClaimView(discord.ui.View):
    def __init__(self, options, *, locked=False):
        super().__init__(timeout=None)
        for option in options:
            self.add_item(ClaimButton(option["id"], option["name"], disabled=locked))

    @classmethod
    def from_database(cls, claim_id):
        claim = get_claim_by_id(claim_id)
        return cls(get_options(claim_id), locked=bool(claim["locked"]))


class ClaimBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())

    async def setup_hook(self):
        init_db()
        for claim in get_all_claim_ids():
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
    description="Create and manage interest-signup boards.",
)


@claim_group.command(name="create", description="Create a new interest-signup board.")
@app_commands.describe(
    name="Name of the board, such as test-1.",
    message="Message/instructions displayed above the options.",
    options="Comma-separated options, e.g. bug tester, GUI tester, graphics tester.",
)
async def claim_create(interaction: discord.Interaction, name: str, message: str, options: str):
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message(
            "This command can only be used in a server channel.", ephemeral=True
        )
        return

    parsed = [option.strip() for option in options.split(",") if option.strip()]
    if not parsed:
        await interaction.response.send_message("Please provide at least one option.", ephemeral=True)
        return
    if len(parsed) > 25:
        await interaction.response.send_message("Please provide 25 or fewer options.", ephemeral=True)
        return
    if len(set(option.casefold() for option in parsed)) != len(parsed):
        await interaction.response.send_message("Option names must be unique.", ephemeral=True)
        return
    if get_claim_by_name(interaction.guild.id, name):
        await interaction.response.send_message(
            f"A board named **{name}** already exists in this server.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    claim_id = create_claim(interaction.guild.id, interaction.channel.id, name, message, parsed)
    view = ClaimView.from_database(claim_id)

    try:
        sent = await interaction.channel.send(embed=build_embed(claim_id), view=view)
        set_claim_message_id(claim_id, sent.id)
        bot.add_view(view, message_id=sent.id)
    except Exception:
        delete_claim(claim_id)
        raise

    await interaction.followup.send(
        f"Created **{name}** with {len(parsed)} options.\n{sent.jump_url}",
        ephemeral=True,
    )


@claim_group.command(name="show-results", description="Show who is interested in each option.")
@app_commands.describe(name="Name of the interest-signup board, such as test-1.")
async def claim_show_results(interaction: discord.Interaction, name: str):
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    claim = get_claim_by_name(interaction.guild.id, name)
    if not claim:
        await interaction.response.send_message(
            f"I couldn't find a board named **{name}**.", ephemeral=True
        )
        return

    options = get_options(claim["id"])
    interests = get_interests(claim["id"])
    embed = discord.Embed(title=f"Interest results — {claim['name']}", color=discord.Color.blurple())
    for option in options:
        user_ids = interests.get(option["id"], [])
        embed.add_field(
            name=f"{option['name']} ({len(user_ids)} interested)",
            value=format_interested_members(user_ids),
            inline=False,
        )
    embed.set_footer(text=f"{sum(len(ids) for ids in interests.values())} total sign-up(s)")
    await interaction.response.send_message(embed=embed)


@claim_group.command(name="list-all-boards", description="List every claim board in this server.")
async def claim_list_all_boards(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    claims = get_claims_by_guild(interaction.guild.id)
    if not claims:
        await interaction.response.send_message("There are no claim boards in this server.", ephemeral=True)
        return

    pages = []
    lines = []
    for claim in claims:
        status = "locked" if claim["locked"] else "open"
        line = f"**{claim['name']}** — {status} — <#{claim['channel_id']}>"
        if lines and len("\n".join(lines + [line])) > 4000:
            pages.append(lines)
            lines = []
        lines.append(line)
    if lines:
        pages.append(lines)

    for index, page in enumerate(pages, start=1):
        embed = discord.Embed(
            title=f"Claim boards ({len(claims)} total)",
            description="\n".join(page),
            color=discord.Color.blurple(),
        )
        if len(pages) > 1:
            embed.set_footer(text=f"Page {index} of {len(pages)}")

        if index == 1:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)


@claim_group.command(name="reset", description="Remove all sign-ups from a board.")
@app_commands.describe(name="Name of the board to reset.")
async def claim_reset(interaction: discord.Interaction, name: str):
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    claim = get_claim_by_name(interaction.guild.id, name)
    if not claim:
        await interaction.response.send_message(f"I couldn't find **{name}**.", ephemeral=True)
        return

    reset_claim(claim["id"])
    try:
        channel = bot.get_channel(claim["channel_id"])
        if channel:
            board_message = await channel.fetch_message(claim["message_id"])
            await board_message.edit(
                embed=build_embed(claim["id"]),
                view=ClaimView.from_database(claim["id"]),
            )
    except discord.HTTPException:
        pass

    await interaction.response.send_message(f"**{name}** has been reset.", ephemeral=True)


@claim_group.command(name="lock-claim", description="Lock a claim and close its sign-ups.")
@app_commands.describe(name="Name of the claim to lock.")
async def claim_lock_claim(interaction: discord.Interaction, name: str):
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    claim = get_claim_by_name(interaction.guild.id, name)
    if not claim:
        await interaction.response.send_message(f"I couldn't find **{name}**.", ephemeral=True)
        return
    if claim["locked"]:
        await interaction.response.send_message(f"**{name}** is already locked.", ephemeral=True)
        return

    async with db_lock:
        lock_claim(claim["id"])

    try:
        channel = bot.get_channel(claim["channel_id"])
        if channel:
            board_message = await channel.fetch_message(claim["message_id"])
            await board_message.edit(
                embed=build_embed(claim["id"]),
                view=ClaimView.from_database(claim["id"]),
            )
    except discord.HTTPException:
        pass

    await interaction.response.send_message(
        f"**{name}** is now locked. Sign-ups are closed.", ephemeral=True
    )


@claim_group.command(name="delete", description="Delete an interest-signup board.")
@app_commands.describe(name="Name of the board to delete.")
async def claim_delete(interaction: discord.Interaction, name: str):
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    claim = get_claim_by_name(interaction.guild.id, name)
    if not claim:
        await interaction.response.send_message(f"I couldn't find **{name}**.", ephemeral=True)
        return

    delete_claim(claim["id"])
    try:
        channel = bot.get_channel(claim["channel_id"])
        if channel:
            board_message = await channel.fetch_message(claim["message_id"])
            await board_message.delete()
    except discord.HTTPException:
        pass

    await interaction.response.send_message(f"Deleted **{name}**.", ephemeral=True)


bot.tree.add_command(claim_group)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
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
