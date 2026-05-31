"""Test if proxy patching breaks on_message event dispatch."""
import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def test_with_proxy_patch():
    """Test on_message works with proxy patching applied."""
    import discord
    from discord import http
    from discord.ext import commands
    
    # Simulate the proxy patch
    import aiohttp
    from aiohttp_socks import ProxyConnector
    
    _proxy_url = "socks5://127.0.0.1:1089"
    
    original_static_login = http.HTTPClient.static_login
    original_ws_connect = http.HTTPClient.ws_connect
    
    async def patched_static_login(self, token):
        print(f"[PATCH] static_login called with token: {token[:10]}...")
        proxy_connector = ProxyConnector.from_url(_proxy_url)
        self._HTTPClient__session = aiohttp.ClientSession(
            connector=proxy_connector,
            ws_response_class=http.DiscordClientWebSocketResponse,
            trace_configs=None,
            cookie_jar=aiohttp.DummyCookieJar(),
        )
        self._global_over = asyncio.Event()
        self._global_over.set()
        old_token = self.token
        self.token = token
        try:
            data = await self.request(http.Route("GET", "/users/@me"))
        except discord.errors.HTTPException as exc:
            self.token = old_token
            if exc.status == 401:
                raise discord.errors.LoginFailure("Improper token has been passed.") from exc
            raise
        return data
    
    async def patched_ws_connect(self, url, *, compress=0):
        print(f"[PATCH] ws_connect called with url: {url}")
        try:
            timeout = aiohttp.ClientWSTimeout(ws_close=30.0)
        except (AttributeError, TypeError):
            timeout = 30.0
        kwargs = {
            'max_msg_size': 0,
            'timeout': timeout,
            'autoclose': False,
            'headers': {'User-Agent': self.user_agent},
            'compress': compress,
        }
        return await self._HTTPClient__session.ws_connect(url, **kwargs)
    
    # Apply patch
    http.HTTPClient.static_login = patched_static_login
    http.HTTPClient.ws_connect = patched_ws_connect
    
    print("[TEST] Proxy patches applied")
    
    # Now test bot with intents
    intents = discord.Intents.default()
    intents.messages = True
    intents.message_content = True
    
    bot = commands.Bot(command_prefix="!", intents=intents)
    
    @bot.event
    async def on_message(message):
        print(f"[EVENT] on_message fired: {message.content}")
        if message.author.bot:
            return
        content = message.content.lower().strip()
        if content in ("hi", "hello"):
            await message.channel.send("Hello!")
    
    # Create mock message
    msg = MagicMock()
    msg.content = "hi"
    msg.author = MagicMock()
    msg.author.bot = False
    msg.author.name = "TestUser"
    msg.channel = AsyncMock()
    
    print("[TEST] Calling on_message directly...")
    await bot.on_message(msg)
    
    if msg.channel.send.called:
        print(f"[TEST] ✅ Response sent: {msg.channel.send.call_args}")
    else:
        print("[TEST] ❌ No response sent")
    
    # Restore
    http.HTTPClient.static_login = original_static_login
    http.HTTPClient.ws_connect = original_ws_connect

if __name__ == "__main__":
    asyncio.run(test_with_proxy_patch())
