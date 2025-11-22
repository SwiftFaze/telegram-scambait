import asyncio
import json
import logging
import random
import re
import tempfile
import aiohttp
import os

import requests
from rlottie_python import LottieAnimation
from openai import OpenAI
from telethon import TelegramClient, events

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
max_message_history = config["max_message_history"]
google_api_key = config["google_api_key"]
google_cx_key = config["google_cx_key"]


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
        self.text = ""
        self.has_media = False
        self.is_photo = False
        self.is_video = False
        self.media_url = ""
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
            if getattr(event.message, "photo", None):
                self.is_photo = True
                self.has_media = True
            elif getattr(event.message, "video", None):
                self.is_video = True
                self.has_media = True
            # Check document (includes files, PDFs, stickers, non-native images, GIFs)
            elif getattr(event.message, "document", None):
                self.is_document = True
                self.has_media = True

        # SET MESSAGE TEXT
        if event.raw_text:
            raw_text = event.raw_text.strip()
            # If message only has media and the text is a placeholder like [GIF] or [JPG], set it to ""
            if self.has_media and raw_text.startswith("["):
                self.text = ""
            else:
                self.text = raw_text
        else:
            self.text = ""



    async def download(self):
        # Handle photos
        if self.has_media and self.is_photo:
            ext = "jpg"
            self.file_path = f"temp_{self.event.id}.{ext}"
            await self.event.download_media(self.file_path)
            logger.debug(f"Downloaded photo to {self.file_path}")

        # Handle videos
        elif self.has_media and self.is_video:
            ext = "mp4"
            # self.file_path = f"temp_{self.event.id}.{ext}"
            # await self.event.download_media(self.file_path)
            # logger.debug(f"Downloaded video to {self.file_path}")

        # Handle documents (including .tgs stickers)
        elif self.has_media and self.is_document:
            mime_type = getattr(self.event.media.document, "mime_type", "")

            # Telegram animated sticker
            if "x-tgsticker" in mime_type:
                tgs_path = f"temp_{self.event.id}.tgs"
                await self.event.download_media(tgs_path)
                logger.debug(f"Downloaded animated sticker to {tgs_path}")

                try:
                    # Load the sticker and save directly as GIF
                    anim = LottieAnimation.from_tgs(tgs_path)
                    gif_path = f"temp_{self.event.id}.gif"
                    anim.save_animation(gif_path)
                    logger.debug(f"Converted sticker to GIF: {gif_path}")

                    # Remove the original .tgs file
                    os.remove(tgs_path)

                    self.file_path = gif_path
                    ext = "gif"
                except Exception as e:
                    logger.error(f"Failed to convert .tgs to GIF: {e}")
                    self.file_path = tgs_path
                    ext = "tgs"

            else:
                # Generic document
                ext = "dat"
                self.file_path = f"temp_{self.event.id}.{ext}"
                await self.event.download_media(self.file_path)
                logger.debug(f"Downloaded document to {self.file_path}")



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

def search_image(query=None):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "q": query + " photo taken",
        "cx": google_cx_key,
        "key": google_api_key,      # your API key
        "searchType": "image",
        "num": 10
    }
    resp = requests.get(url, params=params)
    try:
        data = resp.json()
    except Exception as e:
        logger.error(f"Failed to parse JSON from Google API: {e}, text: {resp.text}")
        return None

    logger.info(f"Google API response for query '{query}': {data}")

    items = data.get("items", [])
    if not items:
        return None

    image = random.choice(items)
    return image.get("link")


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
            logger.info(
                f"Waiting {delay:.1f} seconds before replying to user {message.user_id} in chat {message.chat_id}")
            await asyncio.sleep(delay)

        # Check permissions first
        if not await user_has_message_permission(message):
            logger.info(f"Ignored chat {message.chat_id}: no permission to send messages.")
            return

        messages = await create_gpt_prompt(message)

        response = client_ai.chat.completions.create(
            model=openai_model,
            messages=messages,
        )

        reply = response.choices[0].message.content.strip()
        logger.info(f"Generated reply for user {message.user_id}: {reply}")

        image_url, reply = await process_image_search(reply)

        await add_reply_to_conversation(message, reply)

        save_conversations()

        await message.event.respond(reply)

        await send_image(message, image_url)

        logger.info(f"Message sent in chat {message.chat_id}")

    except asyncio.CancelledError:
        logger.info(f"Timer cancelled for user {message.user_id} in chat {message.chat_id}, reset.")
    except Exception as e:
        logger.warning(
            f"Error in delayed_reply for {message.user_id} in chat {message.chat_id}: {e}")


async def create_gpt_prompt(message):
    # Prepare messages for OpenAI: system prompt + user profile + conversation
    messages = [system_prompt]
    messages.append({
        "role": "user",
        "content": f"User profile info: {json.dumps(user_profile)}"
    })
    messages.extend(conversations[message.user_id][1:])  # skip system_prompt in history
    if message.has_media:
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": message.media_url
                    }
                }
            ]
        })
    return messages


async def process_image_search(reply):
    # Detect and extract JSON block if it exists
    json_block = None
    try:
        # Look for a JSON object in the text
        match = re.search(r"\{.*\"fetch_image\".*\}", reply, re.DOTALL)
        if match:
            json_block = json.loads(match.group(0))
    except Exception as e:
        logger.warning(f"Failed to parse JSON block: {e}")
    # If JSON block found, perform image search
    image_url = None
    if json_block and "fetch_image" in json_block:
        query = json_block["fetch_image"].get("query", "")
        image_url = search_image(query)
        logger.info(f"Fetched image for query '{query}': {image_url}")
        # Remove the JSON from the reply
        reply = re.sub(r"\{.*\"fetch_image\".*\}", "", reply, flags=re.DOTALL).strip()
    return image_url, reply


async def add_reply_to_conversation(message, reply):
    # Add assistant reply to history
    conversations[message.user_id].append({"role": "assistant", "content": reply})
    if len(conversations[message.user_id]) > max_message_history:
        conversations[message.user_id] = [system_prompt] + conversations[message.user_id][
                                                           -max_message_history:]


async def send_image(message, image_url):
    if not image_url:
        logger.warning("No image URL provided to send_image()")
        return

    logger.info(f"Attempting to send image: {image_url}")
    try:
        # Download the image
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                logger.info(f"HTTP GET {image_url} returned status {resp.status}")
                if resp.status == 200:
                    img_data = await resp.read()
                    logger.info(f"Downloaded image data: {len(img_data)} bytes")

                    # Save to a temporary file
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
                        tmp_file.write(img_data)
                        tmp_filename = tmp_file.name
                    logger.info(f"Saved image to temporary file: {tmp_filename}")

                    # Send the image as a photo
                    try:
                        await message.event.respond(file=tmp_filename)
                        logger.info(f"Image sent successfully to chat {message.chat_id}")
                    except Exception as e:
                        logger.error(f"Failed to send image via Telegram: {e}")

                    # Delete the temp file
                    try:
                        os.remove(tmp_filename)
                        logger.info(f"Temporary file deleted: {tmp_filename}")
                    except Exception as e:
                        logger.warning(f"Failed to delete temporary file {tmp_filename}: {e}")

                else:
                    logger.warning(f"Failed to download image, HTTP status: {resp.status}")

    except Exception as e:
        logger.error(f"Unexpected error in send_image(): {e}")


async def user_has_message_permission(message):
    can_send = True;
    if not message.is_private:
        try:
            permissions = await client.get_permissions(message.chat_id, "me")
            if not permissions.send_messages:
                can_send = False
        except Exception:
            can_send = False
    return can_send


# -----------------------------
# Message handler
# -----------------------------

@client.on(events.NewMessage())
async def handler(event):
    me = await client.get_me()
    # Debug mode: only process messages from yourself
    if debug_mode and event.sender_id != me.id:
        return
    if not debug_mode and event.sender_id == me.id:
        return

    message = Message(event, me)
    await message.download()




    # SKIPS VIDEO, VIDEO NOT SUPPORTED
    if message.is_video:
        return

    # Initialize conversation if first message
    if message.user_id not in conversations:
        conversations[message.user_id] = [system_prompt]

    # SET USER TEXT MESSAGE
    if message.text:
        conversations[message.user_id].append({"role": "user", "content": message.text})

    await handle_media(message)

    if len(conversations[message.user_id]) > max_message_history:
        conversations[message.user_id] = [system_prompt] + conversations[message.user_id][-max_message_history:]

    save_conversations()

    # Cancel previous timer if exists
    key = (message.user_id, message.chat_id)
    if key in user_chat_timers and not user_chat_timers[key].done():
        user_chat_timers[key].cancel()

    # Start delayed reply
    user_chat_timers[key] = asyncio.create_task(delayed_reply(message))


async def handle_media(message):
    if message.has_media and not message.is_video:
        # 1. Upload the downloaded file to tmpfiles.org
        with open(message.file_path, "rb") as f:
            files = {"file": f}
            upload_resp = requests.post("https://tmpfiles.org/api/v1/upload", files=files)

        # 2. Extract the file URL from the API response
        data = upload_resp.json()
        raw_url = data["data"]["url"]  # Example: https://tmpfiles.org/abcd1234

        # 2. Convert to download URL
        # raw_url structure: http://tmpfiles.org/<id>/<filename>
        parts = raw_url.split("/")
        file_id = parts[-2]
        filename = parts[-1]

        message.media_url = f"https://tmpfiles.org/dl/{file_id}/{filename}"

        try:
            os.remove(message.file_path)
            logger.debug(f"Deleted local file: {message.file_path}")
        except OSError as e:
            logger.error(f"Error deleting file {message.file_path}: {e}")


# -----------------------------
# Run the bot
# -----------------------------
async def main():
    await client.start()
    logger.info("Bot is online and waiting for messages...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())

# if __name__ == "__main__":
#     client.start()
#     logger.info("Bot is online and waiting for messages...")
#     client.run_until_disconnected()
#
#
#


