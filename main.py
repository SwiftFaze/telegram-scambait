import json
import os
import asyncio
import logging
from telethon import TelegramClient, events
from openai import OpenAI

# -----------------------------
# Setup logging to file
# -----------------------------
LOG_FILE = "bot.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger()

# -----------------------------
# Load config
# -----------------------------
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

api_id = config["telegram_api_id"]
api_hash = config["telegram_api_hash"]
openai_api_key = config["openai_api_key"]
openai_model = config["openai_model"]

# Load system prompt
with open("prompt.json", "r", encoding="utf-8") as f:
    prompt_data = json.load(f)
system_prompt = {"role": "system", "content": prompt_data}

# Initialize clients
client_ai = OpenAI(api_key=openai_api_key)
client = TelegramClient("my_session", api_id, api_hash)

# Conversation history file
HISTORY_FILE = "conversation_history.json"
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        conversations = json.load(f)
else:
    conversations = {}

# Timers keyed by (user_id, chat_id)
user_chat_timers = {}

# Max messages to store per user
MAX_HISTORY = 10

def save_conversations():
    """Save conversation history to file."""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(conversations, f, ensure_ascii=False, indent=2)
    logger.info("Conversation history saved.")

# -----------------------------
# Delayed reply function
# -----------------------------
async def delayed_reply(user_id, chat_id, event):
    try:
        await asyncio.sleep(30)  # wait 30s after last message

        response = client_ai.chat.completions.create(
            model=openai_model,
            messages=conversations[user_id]
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"Generated reply for user {user_id}: {reply}")

        # Add assistant reply to history
        conversations[user_id].append({"role": "assistant", "content": reply})
        if len(conversations[user_id]) > MAX_HISTORY:
            conversations[user_id] = [system_prompt] + conversations[user_id][-MAX_HISTORY:]
        save_conversations()

        # Reply in the same chat if allowed
        can_send = True
        if not event.is_private:
            try:
                permissions = await client.get_permissions(chat_id, "me")
                if not permissions.send_messages:
                    can_send = False
            except Exception:
                can_send = False

        if can_send:
            try:
                await event.respond(reply)
                logger.info(f"Message sent in chat {chat_id}")
            except Exception as e:
                logger.warning(f"Cannot send message in chat {chat_id}: {e}")
        else:
            logger.info(f"Ignored chat {chat_id}: no permission to send messages.")

    except asyncio.CancelledError:
        logger.info(f"Timer cancelled for user {user_id} in chat {chat_id}, reset.")

# -----------------------------
# Message handler
# -----------------------------
@client.on(events.NewMessage(incoming=True))
async def handler(event):
    user_id = str(event.sender_id)
    chat_id = event.chat_id
    user_message = event.raw_text.strip()

    if not user_message:
        logger.info(f"Empty message from {user_id} in chat {chat_id}, skipped.")
        return

    logger.info(f"Received message from {user_id} in chat {chat_id}: {user_message}")

    if user_id not in conversations:
        conversations[user_id] = [system_prompt]

    conversations[user_id].append({"role": "user", "content": user_message})
    if len(conversations[user_id]) > MAX_HISTORY:
        conversations[user_id] = [system_prompt] + conversations[user_id][-MAX_HISTORY:]
    save_conversations()

    key = (user_id, chat_id)
    if key in user_chat_timers and not user_chat_timers[key].done():
        user_chat_timers[key].cancel()

    user_chat_timers[key] = asyncio.create_task(delayed_reply(user_id, chat_id, event))

# -----------------------------
# Run the bot
# -----------------------------
with client:
    logger.info("Bot is online and waiting for messages...")
    client.run_until_disconnected()
