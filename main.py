import os
import discord
import asyncio
from discord.ext import commands, tasks
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Set, Dict

# -------- Load environment variables safely --------
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("POLL_CHANNEL_ID", "0"))
NOTIFY_THREAD_ID = int(os.getenv("NOTIFY_THREAD_ID", "0"))
NOTIFY_ROLE_ID = int(os.getenv("NOTIFY_ROLE_ID", "0"))
VOTE_THRESHOLD = int(os.getenv("VOTE_THRESHOLD", "2"))
MINECRAFT_SERVER_LOGIN = os.getenv("LOGIN_CREDENTIALS", "IP NOT FOUND, PORT NOT FOUND").split(", ")

GETNOTIFIED_ROLE_ID = int(os.getenv("GETNOTIFIED_ROLE_ID", "0"))  # Role ID TODO Fix env var for this
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "0"))  # Channel restriction by ID
POLL_PAUSE_HOUR = int(os.getenv("POLL_PAUSE_HOUR", "21"))  # 9 PM MT
POLL_RESUME_HOUR = int(os.getenv("POLL_RESUME_HOUR", "8"))
WATCH_CHANNEL_ID = int(os.getenv("WATCH_CHANNEL_ID", "0"))

SERVER_CHAT_CHANNEL_ID = int(os.getenv("SERVER_CHAT_CHANNEL_ID", "0"))

# -------- Extras ---------
unapprovedCommands = ["give"]

# -------- Intents and Bot --------
intents = discord.Intents.default()
intents.messages = True
intents.reactions = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -------- Globals --------
poll_message: discord.Message | None = None
running_mode = False  # <-- flag to indicate server running
paused = False

# In-memory vote tracking: maps poll_message.id -> set(user.id)
poll_votes: Dict[int, Set[int]] = {}

# -------- Fixed Mountain Time --------
MT = ZoneInfo("America/Denver")


class PollView(discord.ui.View):
    def __init__(self, message_id: int | None = None):
        super().__init__(timeout=None)  # persistent view
        self.message_id = message_id

    @discord.ui.button(label="Vote to start", style=discord.ButtonStyle.primary, custom_id="poll:vote_button")
    async def vote_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global poll_message, poll_votes, running_mode, paused

        # Prevent votes if we're paused or running
        if paused:
            await interaction.response.send_message("Polls are paused right now.", ephemeral=True)
            return
        if running_mode:
            await interaction.response.send_message("Server is already running.", ephemeral=True)
            return

        user_id = interaction.user.id

        # Ensure poll is active
        if poll_message is None:
            await interaction.response.send_message("Poll is not active at the moment.", ephemeral=True)
            return

        # Track votes per message
        votes = poll_votes.setdefault(poll_message.id, set())

        if user_id in votes:
            votes.remove(user_id)
            await interaction.response.send_message("Your vote has been removed.", ephemeral=True)
        else:
            votes.add(user_id)
            await interaction.response.send_message("Thanks — your vote has been counted!", ephemeral=True)

        # Update poll message with vote count
        try:
            vote_count = len(votes)
            content = f"Click the button to vote for server start!\n\nVotes: **{vote_count}** / {VOTE_THRESHOLD}"
            await poll_message.edit(content=content, view=self)
        except Exception as e:
            print(f"Failed to update poll message with vote count: {e}")

        # Threshold reached → notify owners
        if len(votes) >= VOTE_THRESHOLD:
            try:
                # Disable button and show processing
                button.disabled = True
                await poll_message.edit(content="Processing request... • • •", view=self)
            except Exception as e:
                print(f"Failed to show processing state: {e}")

            whoAskedName = interaction.user.name
            await notify_owner(whoAskedName)

            # Update poll message to indicate owners notified
            try:
                await poll_message.edit(content="✅ Owners have been notified! Poll will reset shortly...", view=self)
            except Exception as e:
                print(f"Failed to update poll message after notifying owners: {e}")

            # Run reset/cooldown on the same message
            await resetAndWait_update_poll()

            # After cooldown, reset votes and re-enable button if not running
            if poll_message and not running_mode and not paused:
                poll_votes[poll_message.id] = set()
                for item in self.children:
                    if isinstance(item, discord.ui.Button):
                        item.disabled = False
                try:
                    await poll_message.edit(content=f"Click the button to vote for server start!\n\nVotes: **0** / {VOTE_THRESHOLD}", view=self)
                except Exception as e:
                    print(f"Failed to restore poll after cooldown: {e}")


# -------- Post or edit poll safely (always update existing message) --------
async def post_poll(channel: discord.TextChannel):
    global poll_message, poll_votes
    if channel is None:
        print("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return None
    try:
        if poll_message is not None:
            # Edit the existing poll message to reset it
            poll_votes[poll_message.id] = set()
            view = PollView(message_id=poll_message.id)
            bot.add_view(view, message_id=poll_message.id)
            await poll_message.edit(content=f"Click the button to vote for server start!\n\nVotes: **0** / {VOTE_THRESHOLD}", view=view)
            print(f"✅ Poll message updated (ID {poll_message.id})")
            return poll_message
        else:
            # Send a fresh poll message
            view = PollView()
            msg = await channel.send(f"Click the button to vote for server start!\n\nVotes: **0** / {VOTE_THRESHOLD}", view=view)
            poll_message = msg
            poll_votes[msg.id] = set()
            # register the view so button callbacks work after restart if desired
            bot.add_view(view, message_id=msg.id)
            print(f"✅ Poll posted with ID {msg.id}")
            return msg
    except Exception as e:
        print(f"❌ Failed to post or update poll: {e}")
        return None


# -------- Notify owners via mention roles --------
async def notify_owner(whoAskedName: str):
    """
    Notify owners by posting only to the notify thread.
    Does NOT send a confirmation message to the poll channel.
    """
    thread = bot.get_channel(NOTIFY_THREAD_ID)
    role_mention = f"<@&{NOTIFY_ROLE_ID}>"

    if thread is None:
        print("❌ Notify thread not found! Check NOTIFY_THREAD_ID")
        return

    try:
        await thread.send(f"{role_mention} {whoAskedName} has requested to start the server. Please start it when you can. Thank you!")
        print("📧 Notification sent in Discord thread!")
    except Exception as e:
        print(f"❌ Failed to send notification in thread: {e}")


# -------- Reset and wait (updates poll message instead of re-posting) --------
async def resetAndWait_update_poll():
    """
    Edit the existing poll message to show a cooldown, disable its button,
    wait the cooldown, then restore the poll message (re-enable buttons).
    No new messages are posted and no channel purge is performed.
    """
    global poll_message, running_mode, paused, poll_votes

    if poll_message is None:
        # fallback: nothing to edit; just log and return
        print("resetAndWait_update_poll called but poll_message is None — nothing to update.")
        return

    channel = poll_message.channel

    # Create a view with disabled buttons to show cooldown state
    view = PollView(message_id=poll_message.id)
    for item in view.children:
        if isinstance(item, discord.ui.Button):
            item.disabled = True

    # Show cooldown message on the poll itself
    try:
        await poll_message.edit(content="⏳ Poll is on cooldown. Please wait 2 minutes before voting again.", view=view)
    except Exception as e:
        print(f"Failed to set cooldown message on poll: {e}")

    # Wait cooldown (2 minutes to preserve original behavior)
    await asyncio.sleep(120)

    # After cooldown, if still not running and not paused, restore poll text and view
    if not running_mode and not paused:
        try:
            # Reset vote tracking for this message id
            poll_votes[poll_message.id] = set()
            # Re-create active view with enabled button(s)
            restored_view = PollView(message_id=poll_message.id)
            # Register persistent view so interactions still work
            bot.add_view(restored_view, message_id=poll_message.id)
            await poll_message.edit(content=f"Click the button to vote for server start!\n\nVotes: **0** / {VOTE_THRESHOLD}", view=restored_view)
        except Exception as e:
            print(f"Failed to restore poll after cooldown: {e}")
    else:
        # If running_mode became True or paused, leave the poll disabled and indicate state
        try:
            status_text = "Server is running — poll paused." if running_mode else "Poll paused."
            await poll_message.edit(content=f"⏯️ {status_text}", view=view)
        except Exception as e:
            print(f"Failed to update poll state after cooldown: {e}")


async def checkCommands(embedMessage):
    desc = embedMessage.description.lower()
    print(desc)

    # if "/give" in desc:
    #     #ban the player
    #     message.channel.send("/ban " + desc[3:4])

# -------- Night Pause / Morning Resume --------
@tasks.loop(hours=1)
async def poll_scheduler():
    global poll_message, running_mode, paused
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return

    now = datetime.now(MT).time()  # use Mountain Time

    # Pause at POLL_PAUSE_HOUR
    if now.hour == POLL_PAUSE_HOUR:
        # purge only bot messages
        await channel.purge(limit=200, check=lambda m: m.author == bot.user)
        paused = True
        poll_message = None
        await channel.send(f"⏸️ Poll paused until {POLL_RESUME_HOUR:02d}:00 MT.")
        print("🌙 Poll paused for the night.")

    # Resume at POLL_RESUME_HOUR
    elif now.hour == POLL_RESUME_HOUR and not running_mode:
        # purge only bot messages and post poll
        await channel.purge(limit=200, check=lambda m: m.author == bot.user)
        poll_message = await post_poll(channel)
        paused = False
        if poll_message:
            print("🌅 Morning poll posted automatically.")


@poll_scheduler.before_loop
async def before_poll_scheduler():
    """Wait until the top of the next hour before starting the loop."""
    await bot.wait_until_ready()
    now = datetime.now(MT)
    seconds_until_next_hour = (60 - now.minute) * 60 - now.second
    if seconds_until_next_hour <= 0:
        seconds_until_next_hour = 0
    print(f"⏳ Waiting {seconds_until_next_hour} seconds to align scheduler to the hour.")
    await asyncio.sleep(seconds_until_next_hour)


# Helper to simulate context for commands
class DummyContext:
    def __init__(self, channel, author=None, guild=None):
        self.channel = channel
        self.author = author or channel.guild.me
        self.guild = guild or channel.guild

    async def send(self, content):
        return await self.channel.send(content)


# -------- Bot Events --------
@bot.event
async def on_message(message):
    # Check for shutdown message from watch channel (keeps previous behavior)
    if message.channel.id == WATCH_CHANNEL_ID and message.author.bot:
        for embed in message.embeds:
            if embed.description:
                dummyContext = DummyContext(message.channel)
                desc = embed.description.lower()
                serverChat = bot.get_channel(SERVER_CHAT_CHANNEL_ID)

                if "the server has opened" in desc and ":green_circle:" in desc:
                    print("Detected server open event!")
                    await message.delete()
                    if serverChat:
                        await serverChat.send("✅ The server is running")
                    await running(dummyContext)

                elif "the server has shutdown" in desc and ":red_circle:" in desc:
                    print("Detected server shutdown event!")
                    if serverChat:
                        await serverChat.purge(limit=200, check=lambda m: m.author == bot.user)
                        await serverChat.send("❌ The server has been shutdown")
                    await resetpoll(dummyContext)

                checkCommands(message)

    await bot.process_commands(message)


@bot.event
async def on_ready():
    global poll_message, paused, poll_votes

    print(f"✅ Logged in as {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return
    if paused:
        return

    # Find an existing poll message (by content pattern) and re-hook the view
    async for msg in channel.history(limit=200):
        if msg.author == bot.user and ("Click the button to vote for server start!" in (msg.content or "") or "Server is running!" in (msg.content or "")):
            poll_message = msg
            poll_votes[msg.id] = set()
            # re-register the view so component interactions continue to work after restart
            view = PollView(message_id=msg.id)
            bot.add_view(view, message_id=msg.id)
            print(f"ℹ️ Found existing message with ID {poll_message.id} (re-registered view)")
            break

    if poll_message is None:
        poll_message = await post_poll(channel)
        print("ℹ️ Posted a fresh poll on startup.")

    # Start the scheduler if not already running
    if not poll_scheduler.is_running():
        poll_scheduler.start()
        print(f"⏰ Scheduler running in Mountain Time (pause {POLL_PAUSE_HOUR:02d}:00, resume {POLL_RESUME_HOUR:02d}:00)")


@bot.event
async def on_reaction_add(reaction, user):
    """
    Backwards compatibility: keep reaction handler as a fallback.
    If someone uses the old reaction approach and the bot still receives it,
    treat it similarly (notify once threshold reached).
    """
    global poll_message, running_mode
    if user.bot or poll_message is None or running_mode:
        return
    if reaction.message.id == poll_message.id and str(reaction.emoji) == "👍":
        # Count unique users who reacted (discord handles reaction.count but user's may toggle)
        # We'll fetch users for the reaction to approximate uniqueness.
        try:
            users = await reaction.users().flatten()
            unique_user_ids = {u.id for u in users if not u.bot}
            if len(unique_user_ids) >= VOTE_THRESHOLD:
                DaUser = await bot.fetch_user(user.id)
                await notify_owner(DaUser.name)
                await resetAndWait_update_poll()
                if not running_mode and not paused:
                    await post_poll(reaction.message.channel)
        except Exception as e:
            print(f"Error while handling reaction fallback: {e}")


# -------- Commands --------
@bot.command()
async def resetpoll(ctx):
    global poll_message, running_mode, paused, poll_votes

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return

    running_mode = False
    paused = False
    # purge only bot messages
    await channel.purge(limit=200, check=lambda m: m.author == bot.user)
    poll_message = await post_poll(channel)
    if poll_message:
        poll_votes[poll_message.id] = set()
        await ctx.send("✅ Poll has been reset for the next round!")


@bot.command()
async def running(ctx):
    global running_mode, poll_message

    role = ctx.guild.get_role(GETNOTIFIED_ROLE_ID)

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return

    running_mode = True
    poll_message = None
    # purge only bot messages
    await channel.purge(limit=200, check=lambda m: m.author == bot.user)
    await channel.send("Server is running! ")
    await channel.send(
        f"Use this info to connect to the server:\n"
        f"IP: {MINECRAFT_SERVER_LOGIN[0]}\n"
        f"Port: {MINECRAFT_SERVER_LOGIN[1]} (The port is for Bedrock users only)\n"
        f"\nMentioning the role {role.mention if role else 'role missing'}. Run !getnotified to get this role and be notified when the server is ready again. Run !stopnotified to remove the role."
    )
    await ctx.send("✅ Server credentials posted. Poll will remain paused until !resetpoll is called.")


@bot.command()
async def pause(ctx):
    global paused

    paused = True
    await ctx.send("⏯️ You have paused the processes!")

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return

    await channel.purge(limit=200, check=lambda m: m.author == bot.user)
    # Edit poll_message if exists to indicate paused state
    if poll_message:
        view = PollView(message_id=poll_message.id)
        for item in view.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            await poll_message.edit(content="⏯️ Processes are now paused! Will resume once !unpause is called", view=view)
        except Exception:
            pass
    else:
        await channel.send("⏯️ Processes are now paused! Will resume once !unpause is called")


@bot.command()
async def unpause(ctx):
    global paused, poll_message

    paused = False
    await ctx.send("⏯️ You have unpaused the processes!")

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return

    await channel.purge(limit=200, check=lambda m: m.author == bot.user)
    poll_message = await post_poll(channel)
    if poll_message:
        await ctx.send("✅ Poll has been reset for the next round!")


# ----------------- getnotified -----------------
@bot.command()
async def getnotified(ctx):
    if ctx.channel.id != GENERAL_CHANNEL_ID:
        return await ctx.send("Please use this command in the designated channel.")

    role = ctx.guild.get_role(GETNOTIFIED_ROLE_ID)
    if not role:
        return await ctx.send("The role does not exist!")

    if role in ctx.author.roles:
        return await ctx.send(f"{ctx.author.mention}, you already have that role!")

    try:
        await ctx.author.add_roles(role)
        await ctx.send(f"{ctx.author.mention}, you have been added to the role!")
    except discord.Forbidden:
        await ctx.send("I don't have permission to add roles.")


# ----------------- stopnotified -----------------
@bot.command()
async def stopnotified(ctx):
    if ctx.channel.id != GENERAL_CHANNEL_ID:
        return await ctx.send("Please use this command in the designated channel.")

    role = ctx.guild.get_role(GETNOTIFIED_ROLE_ID)
    if not role:
        return await ctx.send("The role does not exist!")

    if role not in ctx.author.roles:
        return await ctx.send(f"{ctx.author.mention}, you don't have that role!")

    try:
        await ctx.author.remove_roles(role)
        await ctx.send(f"{ctx.author.mention}, the role has been removed.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to remove roles.")


# -------- Run Bot --------
if TOKEN is None:
    print("❌ DISCORD_TOKEN not set - cannot start bot.")
else:
    bot.run(TOKEN)
