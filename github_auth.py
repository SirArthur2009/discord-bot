"""GitHub App device sign-in with encrypted, durable user credentials."""
import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import aiohttp
import discord
from cryptography.fernet import Fernet, InvalidToken
from discord import app_commands
from discord.ext import commands, tasks

import github_store as store
from github_api import GitHubAPI, GitHubError

log = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()


class SignInButton(discord.ui.Button):
    def __init__(self, auth, guild_id):
        super().__init__(label="Sign in to GitHub", style=discord.ButtonStyle.primary,
                         custom_id=f"github:login:{guild_id}")
        self.auth = auth
        self.guild_id = guild_id

    async def callback(self, interaction):
        await self.auth.start_login(interaction, self.guild_id)


class GitHubAuth(commands.Cog):
    github_group = app_commands.Group(name="github", description="Sign in to your GitHub account.", guild_only=True)

    def __init__(self, bot, workers, client_id, encryption_key):
        self.bot = bot
        self.workers = {worker.settings.guild_id: worker for worker in workers}
        self.client_id = client_id
        self.cipher = Fernet(encryption_key.encode())
        self.locks = {}
        self.session = None
        self.views = []

    def encrypt(self, value):
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))

    def view(self, guild_id):
        view = discord.ui.View(timeout=None)
        view.add_item(SignInButton(self, guild_id))
        return view

    async def cog_load(self):
        store.init_github_db()
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        for guild_id in self.workers:
            view = self.view(guild_id)
            self.bot.add_view(view)
            self.views.append(view)
        self.poll_logins.start()

    async def cog_unload(self):
        self.poll_logins.cancel()
        task = self.poll_logins.get_task()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        for view in self.views:
            view.stop()
        if self.session:
            await self.session.close()

    async def oauth(self, path, data):
        # Credentials stay in request bodies; errors never include GitHub's response body.
        async with self.session.post(f"https://github.com/login/{path}", data=data,
                                     headers={"Accept": "application/json"}) as response:
            if response.status >= 400:
                raise GitHubError(response.status, "GitHub sign-in is temporarily unavailable.")
            return await response.json()

    async def allowed_member(self, guild_id, user_id):
        worker = self.workers.get(guild_id)
        if not worker:
            raise ValueError("GitHub integration is not enabled in this server.")
        channel = await worker.channel()
        member = channel.guild.get_member(user_id) or await channel.guild.fetch_member(user_id)
        if not channel.permissions_for(member).view_channel:
            raise ValueError("You need access to this server's GitHub activity channel.")
        return worker, member

    async def start_login(self, interaction, guild_id):
        await interaction.response.defer(ephemeral=True)
        try:
            worker, _ = await self.allowed_member(guild_id, interaction.user.id)
            async with self.locks.setdefault((guild_id, interaction.user.id), asyncio.Lock()):
                existing = store.execute("SELECT * FROM github_device_flows WHERE guild_id = ? AND user_id = ?",
                                         (guild_id, interaction.user.id), one=True)
                if existing and existing["expires_at"] > time.time():
                    data = self.decrypt(existing["credentials"])
                else:
                    repo = await worker.api.request("GET", f"/repos/{worker.repo}")
                    data = await self.oauth("device/code", {"client_id": self.client_id, "scope": "offline_access"})
                    if data.get("error") or not all(data.get(key) for key in ("device_code", "user_code")):
                        raise ValueError("GitHub sign-in is unavailable. Ask the bot owner to check the GitHub App setup.")
                    data["repo_id"] = repo["id"]
                    data["repo"] = worker.repo
                    interval = max(5, int(data.get("interval", 5)))
                    store.execute("""INSERT OR REPLACE INTO github_device_flows VALUES (?, ?, ?, ?, ?, ?)""",
                                  (guild_id, interaction.user.id, self.encrypt(data), time.time() + int(data["expires_in"]),
                                   time.time() + interval, interval))
            await interaction.followup.send(
                f"Open https://github.com/login/device and enter **{data['user_code']}**.\n"
                f"Authorize **Mind of Metal and Wheels Discord** using your own GitHub account. "
                f"Your account needs access to **{worker.repo}**. This code expires in 15 minutes; keep it private.\n"
                "I’ll DM you when connected. Until then, posts continue through SirArthur2009’s account.",
                ephemeral=True, allowed_mentions=NO_MENTIONS)
        except (ValueError, GitHubError, discord.NotFound, discord.Forbidden, InvalidToken):
            await interaction.followup.send("Could not start sign-in. Check your server access and try /github login again.", ephemeral=True)

    async def notify(self, user_id, text, guild_id=None):
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            kwargs = {"allowed_mentions": NO_MENTIONS}
            if guild_id is not None:
                kwargs["view"] = self.view(guild_id)
            await user.send(text, **kwargs)
        except discord.HTTPException:
            log.info("GitHub sign-in DM could not be delivered to Discord user %s", user_id)

    async def prompt_fallback(self, guild_id, user_id, delivery):
        # Every distinct post gets a DM; network retries of the same post do not.
        if store.execute("SELECT 1 FROM github_login_prompts WHERE delivery = ?", (delivery,), one=True):
            return
        store.execute("INSERT OR IGNORE INTO github_login_prompts VALUES (?)", (delivery,))
        worker = self.workers[guild_id]
        await self.notify(user_id,
                          f"Your post to **{worker.repo}** used **SirArthur2009’s GitHub account** because your "
                          "GitHub sign-in is missing or unavailable. Sign in below (or use /github login) "
                          "to have future posts appear under your own account. You’ll receive this reminder "
                          "for each post until connected.", guild_id)

    def remove_auth(self, guild_id, user_id):
        store.execute("DELETE FROM github_auth WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))

    async def token_for(self, guild_id, user_id):
        async with self.locks.setdefault((guild_id, user_id), asyncio.Lock()):
            row = store.execute("SELECT * FROM github_auth WHERE guild_id = ? AND user_id = ?", (guild_id, user_id), one=True)
            if not row:
                return None
            try:
                credentials = self.decrypt(row["credentials"])
            except InvalidToken:
                log.error("Cannot decrypt GitHub credentials; check GITHUB_AUTH_ENCRYPTION_KEY")
                return None
            if credentials.get("expires_at", 0) <= time.time() + 60:
                if credentials.get("refresh_expires_at", 0) <= time.time():
                    self.remove_auth(guild_id, user_id)
                    return None
                data = await self.oauth("oauth/access_token", {
                    "client_id": self.client_id, "grant_type": "refresh_token",
                    "refresh_token": credentials["refresh_token"],
                })
                if data.get("error"):
                    if data["error"] in {"bad_refresh_token", "invalid_grant", "expired_token"}:
                        self.remove_auth(guild_id, user_id)
                        return None
                    raise GitHubError(503, "GitHub credential refresh is temporarily unavailable.")
                credentials = self.credentials(data, credentials["repo"])
                # Persist rotation immediately: the old refresh token no longer works.
                store.execute("UPDATE github_auth SET credentials = ? WHERE guild_id = ? AND user_id = ?",
                              (self.encrypt(credentials), guild_id, user_id))
            return credentials

    def credentials(self, data, repo):
        if not data.get("access_token"):
            raise ValueError("GitHub did not return a user access token.")
        return {"access_token": data["access_token"], "refresh_token": data.get("refresh_token", ""),
                "expires_at": time.time() + int(data.get("expires_in", 28800)),
                "refresh_expires_at": time.time() + int(data.get("refresh_token_expires_in", 0)), "repo": repo}

    @asynccontextmanager
    async def posting_api(self, guild_id, user_id, repo):
        credentials = await self.token_for(guild_id, user_id)
        api = None
        try:
            if credentials and credentials["repo"].casefold() == repo.casefold():
                api = GitHubAPI(credentials["access_token"], repo)
                try:
                    await api.request("GET", f"/repos/{repo}")
                except GitHubError as error:
                    if error.status not in {401, 403, 404} or error.retry_after:
                        raise
                    if error.status == 401:
                        self.remove_auth(guild_id, user_id)
                    await api.close()
                    api = None
            yield api
        finally:
            if api:
                await api.close()

    async def poll_one(self, row):
        guild_id, user_id = row["guild_id"], row["user_id"]
        async with self.locks.setdefault((guild_id, user_id), asyncio.Lock()):
            current = store.execute("SELECT * FROM github_device_flows WHERE guild_id = ? AND user_id = ?",
                                    (guild_id, user_id), one=True)
            if not current or current["credentials"] != row["credentials"]:
                return
            if row["expires_at"] <= time.time():
                store.execute("DELETE FROM github_device_flows WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
                return
            data = self.decrypt(row["credentials"])
            store.execute("UPDATE github_device_flows SET next_poll = ? WHERE guild_id = ? AND user_id = ?",
                          (time.time() + row["interval"], guild_id, user_id))
            result = data.get("authorized") or await self.oauth("oauth/access_token", {"client_id": self.client_id,
                "device_code": data["device_code"], "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "repository_id": str(data["repo_id"])})
            error = result.get("error")
            if error == "authorization_pending":
                return
            if error == "slow_down":
                interval = max(row["interval"] + 5, int(result.get("interval", 0)))
                store.execute("UPDATE github_device_flows SET interval = ?, next_poll = ? WHERE guild_id = ? AND user_id = ?",
                              (interval, time.time() + interval, guild_id, user_id))
                return
            if error:
                store.execute("DELETE FROM github_device_flows WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
                await self.notify(user_id, "GitHub sign-in was cancelled or expired. Use /github login to try again.")
                return
            credentials = data.get("authorized_credentials") or self.credentials(result, data["repo"])
            # Authorization codes are single-use. Save the granted token encrypted before
            # identity validation so a transient GitHub outage can be retried after restart.
            data["authorized"] = result
            data["authorized_credentials"] = credentials
            store.execute("UPDATE github_device_flows SET credentials = ? WHERE guild_id = ? AND user_id = ?",
                          (self.encrypt(data), guild_id, user_id))
            api = GitHubAPI(credentials["access_token"], data["repo"])
            completed = False
            try:
                worker, _ = await self.allowed_member(guild_id, user_id)
                if worker.repo.casefold() != data["repo"].casefold():
                    raise ValueError("Repository changed during sign-in.")
                identity = await api.request("GET", "/user")
                await api.request("GET", f"/repos/{worker.repo}")
                store.execute("INSERT OR REPLACE INTO github_auth VALUES (?, ?, ?, ?)",
                              (guild_id, user_id, identity["login"], self.encrypt(credentials)))
                store.save_link(guild_id, user_id, identity["login"])
                text = f"Connected as **@{identity['login']}**. Future posts to **{worker.repo}** will use your GitHub account."
                completed = True
            except GitHubError as error:
                if error.status not in {401, 403, 404} or error.retry_after:
                    raise
                completed = True
                text = "GitHub authorization completed, but your account cannot access the configured repository. Ask the owner for access, then use /github login again. Posts still use SirArthur2009’s account."
            except (ValueError, discord.NotFound, discord.Forbidden):
                completed = True
                text = "Your Discord server or repository access changed during sign-in. Use /github login again."
            finally:
                await api.close()
                if completed:
                    store.execute("DELETE FROM github_device_flows WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await self.notify(user_id, text)

    @tasks.loop(seconds=2)
    async def poll_logins(self):
        rows = store.execute("SELECT * FROM github_device_flows WHERE next_poll <= ? ORDER BY next_poll LIMIT 10",
                             (time.time(),), rows=True)
        for row in rows:
            try:
                await self.poll_one(row)
            except Exception as error:
                # Never log token responses or credential-bearing exception text.
                log.warning("GitHub sign-in polling failed: %s", type(error).__name__)

    @poll_logins.before_loop
    async def before_poll_logins(self):
        await self.bot.wait_until_ready()

    @github_group.command(name="login", description="Sign in so GitHub posts use your own account.")
    async def login(self, interaction: discord.Interaction):
        await self.start_login(interaction, interaction.guild_id)

    @github_group.command(name="logout", description="Disconnect your GitHub sign-in from this server.")
    async def logout(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with self.locks.setdefault((interaction.guild_id, interaction.user.id), asyncio.Lock()):
            self.remove_auth(interaction.guild_id, interaction.user.id)
            store.delete_link(interaction.guild_id, interaction.user.id)
            store.execute("DELETE FROM github_device_flows WHERE guild_id = ? AND user_id = ?",
                          (interaction.guild_id, interaction.user.id))
        await interaction.followup.send("Disconnected. Future posts use SirArthur2009’s account with a login reminder. "
                                        "You can also revoke the app in GitHub Settings → Applications.", ephemeral=True)

    @github_group.command(name="status", description="Show which GitHub account will author your posts.")
    async def status(self, interaction: discord.Interaction):
        row = store.execute("SELECT login FROM github_auth WHERE guild_id = ? AND user_id = ?",
                            (interaction.guild_id, interaction.user.id), one=True)
        text = f"Connected as **@{row['login']}**." if row else "Not signed in. Posts use SirArthur2009’s account. Use /github login to connect."
        await interaction.response.send_message(text, ephemeral=True)


async def install_auth(bot, workers):
    client_id = os.getenv("GITHUB_APP_CLIENT_ID", "")
    key = os.getenv("GITHUB_AUTH_ENCRYPTION_KEY", "")
    if not client_id and not key:
        return
    if not client_id or not key:
        raise RuntimeError("GitHub sign-in needs GITHUB_APP_CLIENT_ID and GITHUB_AUTH_ENCRYPTION_KEY.")
    auth = GitHubAuth(bot, workers, client_id, key)
    await bot.add_cog(auth)
    for worker in workers:
        worker.auth = auth
