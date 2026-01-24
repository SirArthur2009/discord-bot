# In your cog
import os
import discord
from discord.ext import commands
from utils.database import get_connection
from bot_app import bot

class ClaimsApprovalView(discord.ui.View):
    def __init__(self, claim_id: int, user_id: str, x1: str, z1: str, x2: str, z2: str):
        super().__init__(timeout=None)
        self.claim_id = claim_id
        self.user_id = user_id
        self.x1 = x1
        self.z1 = z1
        self.x2 = x2
        self.z2 = z2

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="claims:approve_button")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Approve a claim: check for overlap, move to claims table, remove from requests."""
        conn = get_connection()
        if not conn:
            await interaction.response.send_message("❌ DB connection failed", ephemeral=True)
            return

        cursor = conn.cursor()
        try:
            # Check for overlaps in existing claims
            cursor.execute("""
                SELECT id FROM claims WHERE 
                NOT (X2 < %s OR X1 > %s OR Z2 < %s OR Z1 > %s)
            """, (float(self.x1), float(self.x2), float(self.z1), float(self.z2)))
            
            overlapping = cursor.fetchall()
            if overlapping:
                await interaction.response.send_message(
                    f"❌ Overlap detected with existing claims. Cannot approve.",
                    ephemeral=True
                )
                cursor.close()
                conn.close()
                return

            # No overlap - move to claims table
            cursor.execute("CREATE TABLE IF NOT EXISTS claims (id INT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(50), X1 VARCHAR(50), Z1 VARCHAR(50), X2 VARCHAR(50), Z2 VARCHAR(50));")
            cursor.execute(
                "INSERT INTO claims (user_id, X1, Z1, X2, Z2) VALUES (%s, %s, %s, %s, %s)",
                (self.user_id, self.x1, self.z1, self.x2, self.z2)
            )
            
            # Delete from requests
            cursor.execute("DELETE FROM requests WHERE id = %s", (self.claim_id,))
            conn.commit()

            await interaction.response.send_message(
                f"✅ Claim approved! Moved to claims table. (Claim ID: {self.claim_id})",
                ephemeral=True
            )

            # Disable buttons
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            await interaction.message.edit(view=self)

        except Exception as e:
            await interaction.response.send_message(f"❌ Error approving claim: {e}", ephemeral=True)
        finally:
            cursor.close()
            conn.close()

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="claims:deny_button")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Deny a claim: remove from requests table."""
        conn = get_connection()
        if not conn:
            await interaction.response.send_message("❌ DB connection failed", ephemeral=True)
            return

        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM requests WHERE id = %s", (self.claim_id,))
            conn.commit()

            await interaction.response.send_message(
                f"❌ Claim denied and removed. (Claim ID: {self.claim_id})",
                ephemeral=True
            )

            # Disable buttons
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            await interaction.message.edit(view=self)

        except Exception as e:
            await interaction.response.send_message(f"❌ Error denying claim: {e}", ephemeral=True)
        finally:
            cursor.close()
            conn.close()


class ClaimsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def pingdb(self, ctx):
        conn = get_connection()
        if not conn:
            await ctx.send("❌ DB connection failed")
            return

        cursor = conn.cursor()
        cursor.execute("SELECT NOW();")
        now = cursor.fetchone()[0]
        cursor.close()
        conn.close()

        await ctx.send(f"✅ Database time: `{now}`")\

    @commands.command()
    async def claim(self, ctx, X1: str, Z1: str, X2: str, Z2: str):
        """Gets called by !claims X1 Z1 X2 Z2 to claims land plots."""
        if ctx.channel.id != int(os.getenv("CLAIMS_CHANNEL_ID", "0")):
            await ctx.send("❌ Claims can only be made in the designated claims channel.")
            return
        conn = get_connection()
        if not conn:
            await ctx.send("❌ DB connection failed")
            return

        cursor = conn.cursor()
        try:
            cursor.execute("CREATE TABLE IF NOT EXISTS requests (id INT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(50), X1 VARCHAR(50), Z1 VARCHAR(50), X2 VARCHAR(50), Z2 VARCHAR(50));")
            cursor.execute("INSERT INTO requests (user_id, X1, Z1, X2, Z2) VALUES (%s, %s, %s, %s, %s);", (str(ctx.author.id), X1, Z1, X2, Z2))
            conn.commit()
            await ctx.send(f"✅ {ctx.author.mention}, you have requested the claim `X1={X1}, Z1={Z1}, X2={X2}, Z2={Z2}`")
            await self.askForApproval(cursor.lastrowid, str(ctx.author.id), X1, Z1, X2, Z2)
        except Exception as e:
            await ctx.send(f"❌ Failed to claim item: {e}")
        finally:
            cursor.close()
            conn.close()

    async def askForApproval(self, claimID, user_id, x1, z1, x2, z2):
        channel = self.bot.get_channel(int(os.getenv("ADMIN_CHANNEL_ID", "0")))
        if channel is None:
            print("❌ Claims approval channel not found! Check ADMIN_CHANNEL_ID")
            return
        
        view = ClaimsApprovalView(claimID, user_id, x1, z1, x2, z2)
        bot.add_view(view, message_id=None)
        await channel.send(
            f"🛎️ New claim request received! Claim ID: {claimID}\n"
            f"User: <@{user_id}>\n"
            f"Coordinates: X1={x1}, Z1={z1}, X2={x2}, Z2={z2}",
            view=view
        )