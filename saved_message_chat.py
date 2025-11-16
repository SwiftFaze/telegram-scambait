import json
import asyncio
import logging
from telethon import TelegramClient, events

# -----------------------------
# Load config
# -----------------------------
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

api_id = config["telegram_api_id"]
api_hash = config["telegram_api_hash"]
log_to_console = config.get("log_to_console", False)

# -----------------------------
# Setup logging
# -----------------------------
LOG_FILE = "bot.log"

handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
if log_to_console:
    handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=handlers
)
logger = logging.getLogger()

# -----------------------------
# Initialize client
# -----------------------------
client = TelegramClient("my_session", api_id, api_hash)

# -----------------------------
# Run the bot
# -----------------------------
async def main():
    # Start the client
    await client.start()
    me = await client.get_me()
    username = me.username if me.username else "(no username)"
    logger.info(f"Logged in as {username} ({me.id})")

    # Send a test message to Saved Messages
    await client.send_message("me", "Hello from Telethon!")
    logger.info("Sent test message to Saved Messages.")

    # Print the last 10 messages in Saved Messages
    logger.info("Last 10 messages in Saved Messages:")
    async for message in client.iter_messages("me", limit=10):
        logger.info(f"[{message.date}] {message.text}")

    # Event handler for new messages
    @client.on(events.NewMessage())
    async def handler(event):
        # Detect messages in Saved Messages
        if event.is_private and event.chat_id == me.id:
            logger.info(f"[Saved Messages] You sent: {event.raw_text}")
        else:
            logger.info(f"[Other chat] From {event.sender_id}: {event.raw_text}")


    logger.info("Bot is running. Send messages to Saved Messages to see them logged.")
    await client.run_until_disconnected()

# -----------------------------
# Entry point
# -----------------------------
if __name__ == "__main__":
    asyncio.run(main())
