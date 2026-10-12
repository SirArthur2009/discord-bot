"""Optional per-server configuration for both claim and GitHub commands."""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import discord

DEFAULT_REPO = "SirArthur2009/discord-bot"


@dataclass(frozen=True)
class ServerConfig:
    guild_id: int
    command_role_id: int
    github_channel_id: int | None = None
    github_repo: str = DEFAULT_REPO


def positive_id(value, name):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise RuntimeError(f"{name} must be a positive numeric Discord ID.")
    return int(value)


def load_servers():
    path = os.getenv("SERVER_CONFIG_PATH", "").strip()
    if not path:
        return []
    try:
        items = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Cannot read SERVER_CONFIG_PATH {path}: {error}") from error
    if not isinstance(items, list) or not items:
        raise RuntimeError("The server configuration must be a nonempty JSON list.")
    servers = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("Each server configuration must be an object.")
        guild = positive_id(item.get("guild_id"), "guild_id")
        role = positive_id(item.get("command_role_id"), "command_role_id")
        channel = item.get("github_channel_id")
        channel = positive_id(channel, "github_channel_id") if channel is not None else None
        repo = item.get("github_repo", DEFAULT_REPO)
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise RuntimeError("github_repo must be owner/repository.")
        if guild in {server.guild_id for server in servers}:
            raise RuntimeError("Each Discord server may appear only once in the configuration.")
        if channel is not None and channel in {server.github_channel_id for server in servers}:
            raise RuntimeError("Each GitHub activity channel may appear only once in the configuration.")
        servers.append(ServerConfig(guild, role, channel, repo))
    return servers


def command_role_for_guild(guild_id, servers, fallback):
    if servers:
        return next((server.command_role_id for server in servers if server.guild_id == guild_id), None)
    return fallback


async def sync_commands(tree, servers, legacy_guild_id):
    guild_ids = [server.guild_id for server in servers]
    if not guild_ids and legacy_guild_id:
        guild_ids = [positive_id(legacy_guild_id, "GUILD_ID")]
    if not guild_ids:
        await tree.sync()
        print("Synced global commands.")
        return
    for guild_id in guild_ids:
        guild = discord.Object(id=guild_id)
        tree.copy_global_to(guild=guild)
        server = next((item for item in servers if item.guild_id == guild_id), None)
        if server is not None and server.github_channel_id is None:
            tree.remove_command("issue", guild=guild)
            tree.remove_command("github", guild=guild)
        await tree.sync(guild=guild)
        print(f"Synced commands to guild {guild_id}")
