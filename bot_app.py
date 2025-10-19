# discord-bot/bot_app.py
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.messages = True
intents.reactions = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
