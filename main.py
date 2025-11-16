import json
import os
import random
import asyncio
import logging
from telethon import TelegramClient, events
from openai import OpenAI
import base64

# -----------------------------
# Load config
# -----------------------------
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

api_id = config["telegram_api_id"]
api_hash = config["telegram_api_hash"]
openai_api_key = config["openai_api_key"]
openai_model = config["openai_model"]
debug_mode = config["debug_mode"]



# -----------------------------
# Setup logging to file
# -----------------------------

LOG_FILE = "bot.log"

handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]

if debug_mode:
    handlers.append(logging.StreamHandler())  # log to console in debug mode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=handlers
)

logger = logging.getLogger()


class Message:
    def __init__(self, event, me):
        self.event = event
        self.sender_id = event.sender_id
        self.text = event.raw_text.strip() if event.raw_text else ""
        self.is_photo = False
        self.is_video = False
        self.is_document = False
        self.is_private = event.is_private
        self.file_path = None
        self.event = event


        # Determine mode
        if debug_mode:
            # In debug mode, only process messages from yourself
            if event.sender_id != me.id:
                return  # ignore all other users
            self.user_id = str(me.id)
            self.chat_id = me.id  # Saved Messages
        else:
            # Normal mode: handle all users
            self.user_id = str(event.sender_id)
            self.chat_id = event.chat_id

        if event.message.media:
            # Check photo
            if getattr(event.message, "photo", None):
                self.is_photo = True
            # Check video
            elif getattr(event.message, "video", None):
                self.is_video = True
            # Check document (includes files, PDFs, stickers, non-native images, GIFs)
            elif getattr(event.message, "document", None):
                self.is_document = True

    async def download(self):
        if self.is_photo or self.is_video or self.is_document:
            ext = "jpg" if self.is_photo else "mp4" if self.is_video else "dat"
            self.file_path = f"temp_{self.event.id}.{ext}"
            await self.event.download_media(self.file_path)
            logger.info(f"Downloaded media to {self.file_path}")
            if not self.text:
                self.text = f"[{ext.upper()}] {self.file_path}"



# -----------------------------
# Load system prompt and user profile
# -----------------------------
with open("personality_prompt.json", "r", encoding="utf-8") as f:
    prompt_data = json.load(f)

system_prompt = {"role": "system", "content": prompt_data["system_prompt"]}
user_profile = prompt_data.get("user_profile", {})

# -----------------------------
# Initialize clients
# -----------------------------
client_ai = OpenAI(api_key=openai_api_key)
client = TelegramClient("my_session", api_id, api_hash)

# -----------------------------
# Conversation history file
# -----------------------------
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
async def delayed_reply(message):
    try:
        if not debug_mode:
            # Only apply delay in normal mode
            delay = random.uniform(30, 1200)
            logger.info(f"Waiting {delay:.1f} seconds before replying to user {message.user_id} in chat {message.chat_id}")
            await asyncio.sleep(delay)

        # Check permissions first
        can_send = True
        if not message.is_private:
            try:
                permissions = await client.get_permissions(message.chat_id, "me")
                if not permissions.send_messages:
                    can_send = False
            except Exception:
                can_send = False

        if not can_send:
            logger.info(f"Ignored chat {message.chat_id}: no permission to send messages.")
            return  # Do not call OpenAI

        # Prepare messages for OpenAI: system prompt + user profile + conversation
        messages = [system_prompt]
        messages.append({
            "role": "user",
            "content": f"User profile info: {json.dumps(user_profile)}"
        })
        messages.extend(conversations[message.user_id][1:])  # skip system_prompt in history


        if message.is_photo:
            with open(message.file_path, "rb") as f:
                image_bytes = f.read()
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            messages.append({
                "role": "user",
                "content": ''
            })
            response = client_ai.chat.completions.create(
                model=openai_model,
                messages=messages,
            )
        else:
            # Generate the AI reply
            response = client_ai.chat.completions.create(
                model=openai_model,
                messages=messages,

            )
        reply = response.choices[0].message.content.strip()
        logger.info(f"Generated reply for user {message.user_id}: {reply}")

        # Add assistant reply to history
        conversations[message.user_id].append({"role": "assistant", "content": reply})
        if len(conversations[message.user_id]) > MAX_HISTORY:
            conversations[message.user_id] = [system_prompt] + conversations[message.user_id][-MAX_HISTORY:]
        save_conversations()

        # Send reply in chat
        await message.event.respond(reply)
        logger.info(f"Message sent in chat {message.chat_id}")

    except asyncio.CancelledError:
        logger.info(f"Timer cancelled for user {message.user_id} in chat {message.chat_id}, reset.")
    except Exception as e:
        logger.warning(f"Error in delayed_reply for {message.user_id} in chat {message.chat_id}: {e}")

# -----------------------------
# Message handler
# -----------------------------

@client.on(events.NewMessage())
async def handler(event):

    me = await client.get_me()
    message = Message(event, me)
    await message.download()

    # Debug mode: only process messages from yourself
    if debug_mode and message.sender_id != me.id:
        return





    # Initialize conversation if first message
    if message.user_id not in conversations:
        conversations[message.user_id] = [system_prompt]

    # Append user message
    conversations[message.user_id].append({"role": "user", "content": message.text})
    if len(conversations[message.user_id]) > MAX_HISTORY:
        conversations[message.user_id] = [system_prompt] + conversations[message.user_id][-MAX_HISTORY:]
    save_conversations()

    # Cancel previous timer if exists
    key = (message.user_id, message.chat_id)
    if key in user_chat_timers and not user_chat_timers[key].done():
        user_chat_timers[key].cancel()

    # Start delayed reply
    user_chat_timers[key] = asyncio.create_task(delayed_reply(message))


# -----------------------------
# Run the bot
# -----------------------------
async def main():
    await client.start()
    logger.info("Bot is online and waiting for messages...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
