"""Local test script to simulate Discord messages for the bot."""
import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, PropertyMock

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def mock_message(content: str, author_name: str = "TestUser", bot: bool = False):
    """Create a mock Discord Message object."""
    message = MagicMock()
    message.content = content
    message.author = MagicMock()
    message.author.bot = bot
    message.author.name = author_name
    message.author.id = 12345
    message.channel = MagicMock()
    message.channel.send = AsyncMock()
    return message

async def test_greeting(bot, content: str):
    """Test if bot responds to a greeting."""
    msg = await mock_message(content)
    
    print(f"\n{'='*50}")
    print(f"Testing message: '{content}'")
    print(f"Author: {msg.author.name} (bot={msg.author.bot})")
    
    # Call on_message directly
    await bot.on_message(msg)
    
    # Check if channel.send was called
    if msg.channel.send.called:
        call_args = msg.channel.send.call_args
        print(f"✅ RESPONSE SENT: {call_args}")
        return True
    else:
        print(f"❌ NO RESPONSE")
        return False

async def main():
    from app.discord.bot import start_bot, _bot
    
    # We need to start the bot but since we don't have a real token,
    # we'll create a minimal test
    
    # Import the intents setup
    import discord
    from discord.ext import commands
    
    intents = discord.Intents.default()
    intents.messages = True
    intents.message_content = True
    
    test_bot = commands.Bot(command_prefix="!", intents=intents)
    
    # Register our on_message handler
    @test_bot.event
    async def on_message(message):
        import structlog
        logger = structlog.get_logger()
        logger.info("test.message.received", author=str(message.author), content=message.content)
        if message.author.bot:
            return
        content = message.content.lower().strip()
        if content in ("hi", "hello", "hey", "你好", "嗨"):
            await message.channel.send(
                "Hello! I'm Agent Mod 0x01. Use `/generate <prompt>` to create a Stardew Valley mod."
            )
    
    # Test greetings
    greetings = ["hi", "hello", "hey", "你好", "嗨", "howdy", "greetings"]
    results = {}
    
    for greeting in greetings:
        results[greeting] = await test_greeting(test_bot, greeting)
    
    print(f"\n{'='*50}")
    print("SUMMARY:")
    for greeting, passed in results.items():
        status = "✅" if passed else "❌"
        print(f"  {status} '{greeting}'")

if __name__ == "__main__":
    asyncio.run(main())
