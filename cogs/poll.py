# discord-bot/cogs/poll.py
import os
import asyncio
import discord
from typing import Set, Dict
from bot_app import bot
from discord.ext import commands
from utils.helpers import notify_owner_thread

VOTE_THRESHOLD = int(os.getenv("VOTE_THRESHOLD", "1"))

# Module-level state owned by this module (single source of truth)
poll_message: discord.Message | None = None
poll_votes: Dict[int, Set[int]] = {}
running_mode = False
paused = False


class PollView(discord.ui.View):
    def __init__(self, message_id: int | None = None):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="Vote to start", style=discord.ButtonStyle.primary, custom_id="poll:vote_button")
    async def vote_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Button handler: toggles vote, updates poll message, and triggers notify+cooldown when threshold met.
        """
        global poll_message, poll_votes, running_mode, paused

        if paused:
            await interaction.response.send_message("Polls are paused right now.", ephemeral=True)
            return
        if running_mode:
            await interaction.response.send_message("Server is already running.", ephemeral=True)
            return

        user_id = interaction.user.id

        if poll_message is None:
            await interaction.response.send_message("Poll is not active at the moment.", ephemeral=True)
            return

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
                # Disable the button and show a tiny processing UI
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        child.disabled = True
                await poll_message.edit(content="Processing request... • • •", view=self)
            except Exception as e:
                print(f"Failed to show processing state: {e}")

            whoAskedName = interaction.user.name
            await notify_owner_thread(whoAskedName)

            # update poll message to show owners notified (instead of sending new channel message)
            try:
                await poll_message.edit(content="✅ Owners have been notified! Poll will reset shortly...", view=self)
            except Exception as e:
                print(f"Failed to update poll message after notifying owners: {e}")

            # Run cooldown/reset flow
            await reset_and_wait_update_poll()

            # After cooldown, restore poll (if not running)
            if poll_message and not running_mode and not paused:
                poll_votes[poll_message.id] = set()
                # re-enable button(s)
                view = PollView(message_id=poll_message.id)
                bot.add_view(view, message_id=poll_message.id)
                try:
                    await poll_message.edit(content=f"Click the button to vote for server start!\n\nVotes: **0** / {VOTE_THRESHOLD}", view=view)
                except Exception as e:
                    print(f"Failed to restore poll after cooldown: {e}")


# Utility functions owned by this module
async def post_poll(channel: discord.TextChannel):
    """
    Create a fresh poll message or edit the existing poll message to reset it.
    Returns the poll message object.
    """
    global poll_message, poll_votes
    if channel is None:
        print("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return None

    try:
        if poll_message is not None:
            # Reset votes and edit the existing poll message
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
            bot.add_view(view, message_id=msg.id)
            print(f"✅ Poll posted with ID {msg.id}")
            return msg
    except Exception as e:
        print(f"❌ Failed to post or update poll: {e}")
        return None


async def reset_and_wait_update_poll():
    """
    Edits the existing poll message to show a cooldown and disables buttons,
    waits the cooldown, then restores the poll message (or shows poll paused / running).
    """
    global poll_message, poll_votes, running_mode, paused
    if poll_message is None:
        print("reset_and_wait_update_poll called but poll_message is None — nothing to update.")
        return

    # Disable buttons in a view to show cooldown state
    view = PollView(message_id=poll_message.id)
    for item in view.children:
        if isinstance(item, discord.ui.Button):
            item.disabled = True

    try:
        await poll_message.edit(content="⏳ Poll is on cooldown. Please wait 2 minutes before voting again.", view=view)
    except Exception as e:
        print(f"Failed to set cooldown message on poll: {e}")

    # Wait cooldown (2 minutes)
    await asyncio.sleep(120)

    if not running_mode and not paused:
        try:
            poll_votes[poll_message.id] = set()
            restored_view = PollView(message_id=poll_message.id)
            bot.add_view(restored_view, message_id=poll_message.id)
            await poll_message.edit(content=f"Click the button to vote for server start!\n\nVotes: **0** / {VOTE_THRESHOLD}", view=restored_view)
        except Exception as e:
            print(f"Failed to restore poll after cooldown: {e}")
    else:
        # If running or paused, leave disabled and indicate status
        try:
            status_text = "Server is running — poll paused." if running_mode else "Poll paused."
            await poll_message.edit(content=f"⏯️ {status_text}", view=view)
        except Exception as e:
            print(f"Failed to update poll state after cooldown: {e}")


# Cog exposing resetpoll command as before
class PollCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def resetpoll(self, ctx):
        global poll_message, running_mode, paused, poll_votes
        channel = self.bot.get_channel(int(os.getenv("POLL_CHANNEL_ID", "0")))
        if channel is None:
            await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
            return

        running_mode = False
        paused = False
        await channel.purge(limit=200, check=lambda m: m.author == self.bot.user)
        poll_message_local = await post_poll(channel)
        if poll_message_local:
            poll_votes[poll_message_local.id] = set()
            await ctx.send("✅ Poll has been reset for the next round!")
