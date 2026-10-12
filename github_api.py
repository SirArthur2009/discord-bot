"""Small asynchronous GitHub REST client; never logs authentication headers."""
import asyncio
import time
from urllib.parse import quote

import aiohttp


PRIORITIES = {"low": "2da44e", "medium": "d4a72c", "high": "d1242f", "urgent": "8250df"}


class GitHubError(Exception):
    def __init__(self, status, message, retry_after=None):
        super().__init__(f"GitHub returned {status}: {message}")
        self.status = status
        self.retry_after = retry_after


class GitHubAPI:
    def __init__(self, token, repo):
        self.repo = repo
        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "ClaimBot"},
            timeout=aiohttp.ClientTimeout(total=30),
        )
        self.priority_lock = asyncio.Lock()

    async def close(self):
        await self.session.close()

    async def request(self, method, path, **kwargs):
        async with self.session.request(method, f"https://api.github.com{path}", **kwargs) as response:
            if response.status == 204:
                return None
            try:
                data = await response.json()
            except (ValueError, aiohttp.ContentTypeError):
                data = {}
            if response.status >= 400:
                retry_after = response.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else None
                if delay is None and response.headers.get("X-RateLimit-Remaining") == "0":
                    reset = response.headers.get("X-RateLimit-Reset", "")
                    if reset.isdigit():
                        delay = max(1, int(reset) - int(time.time()))
                raise GitHubError(response.status, data.get("message", "Request failed"),
                                  delay)
            return data

    async def repo_request(self, method, path, **kwargs):
        return await self.request(method, f"/repos/{self.repo}/{path}", **kwargs)

    async def issue(self, number):
        return await self.repo_request("GET", f"issues/{number}")

    async def comments(self, number):
        page = 1
        while True:
            batch = await self.repo_request("GET", f"issues/{number}/comments",
                                            params={"per_page": 100, "page": page})
            for comment in batch:
                yield comment
            if len(batch) < 100:
                break
            page += 1

    async def set_priority(self, number, priority):
        # Add/remove only our labels so unrelated labels survive concurrent changes.
        async with self.priority_lock:
            label = f"priority:{priority}"
            try:
                await self.repo_request("GET", f"labels/{quote(label, safe='')}")
            except GitHubError as error:
                if error.status != 404:
                    raise
                try:
                    await self.repo_request("POST", "labels", json={"name": label, "color": PRIORITIES[priority]})
                except GitHubError as conflict:
                    if conflict.status != 422:
                        raise
                    # A concurrent worker may have created it; validate that it exists.
                    await self.repo_request("GET", f"labels/{quote(label, safe='')}")
            await self.repo_request("POST", f"issues/{number}/labels", json={"labels": [label]})
            labels = await self.repo_request("GET", f"issues/{number}/labels")
            for existing in labels:
                if existing["name"] in {f"priority:{value}" for value in PRIORITIES} and existing["name"] != label:
                    try:
                        await self.repo_request("DELETE", f"issues/{number}/labels/{quote(existing['name'], safe='')}")
                    except GitHubError as error:
                        if error.status != 404:
                            raise
        return await self.issue(number)
