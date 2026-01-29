import asyncio
import logging
import os
import sys

import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.utils.markdown import hbold, hcode

# --- CONFIGURATION ---
# Get token from environment variable on Railway
BOT_TOKEN = "8115043032:AAFDDtZrSZkLqZfNVKD4HYBoEttA7ZLWYMo"
# Your specific admin group ID
ADMIN_GROUP_ID = -1003570824341
DATABASE_NAME = "modmail.db"

# --- SETUP ---
# Configure logging to see errors in Railway logs
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

# Initialize Bot and Dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- DATABASE FUNCTIONS (SQLite via aiosqlite) ---
async def init_db():
    """Creates the necessary table if it doesn't exist."""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS user_topics (user_id INTEGER PRIMARY KEY, topic_id INTEGER)"
        )
        await db.commit()

async def get_topic_by_user(user_id: int) -> int | None:
    """Gets the topic ID associated with a user ID."""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        async with db.execute("SELECT topic_id FROM user_topics WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def get_user_by_topic(topic_id: int) -> int | None:
    """Gets the user ID associated with a topic ID (for replies)."""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        async with db.execute("SELECT user_id FROM user_topics WHERE topic_id = ?", (topic_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def save_user_topic(user_id: int, topic_id: int):
    """Saves a new user-topic mapping."""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO user_topics (user_id, topic_id) VALUES (?, ?)", (user_id, topic_id))
        await db.commit()


# --- HANDLERS ---

@dp.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def command_start_handler(message: types.Message) -> None:
    """Send a welcome message in private chat."""
    await message.answer(f"Hello, {hbold(message.from_user.full_name)}! Any message you send here will be forwarded to the admins.", parse_mode=ParseMode.HTML)

@dp.message(F.chat.type == ChatType.PRIVATE)
async def handle_user_message(message: types.Message):
    """
    Handles ALL messages sent by users in private chat.
    Creates a topic if needed, sends info, then forwards the message.
    """
    user_id = message.from_user.id
    user = message.from_user

    topic_id = await get_topic_by_user(user_id)

    # If no topic exists for this user, create one
    if topic_id is None:
        try:
            # Create new topic name based on user info
            topic_name = f"{user.full_name} [{user_id}]"[:127] # Ensure it fits limit
            
            logger.info(f"Creating new topic for user {user_id} in group {ADMIN_GROUP_ID}")
            new_topic = await bot.create_forum_topic(chat_id=ADMIN_GROUP_ID, name=topic_name)
            topic_id = new_topic.message_thread_id

            # Save mapping to DB
            await save_user_topic(user_id, topic_id)

            # Prepare user info message
            username_txt = f"@{user.username}" if user.username else "None"
            info_text = (
                f"🆕 **New Ticket Created**\n\n"
                f"👤 **User:** {hbold(user.full_name)}\n"
                f"🆔 **ID:** {hcode(user_id)}\n"
                f"🔗 **Username:** {username_txt}\n"
                f"🗣 **Language:** {user.language_code}"
            )
            # Send info message as the first message in the topic
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                message_thread_id=topic_id,
                text=info_text,
                parse_mode=ParseMode.HTML
            )
            
        except TelegramBadRequest as e:
            logger.error(f"Failed to create topic: {e}")
            await message.reply("⚠️ Error: Could not connect to support staff. Please ensure the bot is an admin in the group and Topics are enabled.")
            return

    # Forward the user's actual message to the topic
    try:
        # copy_to perfectly replicates the message content (photo, video, poll, etc.)
        await message.copy_to(chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id)
        # Optional: React to user message to confirm receipt
        # await message.react([types.ReactionTypeEmoji(emoji="👍")])
    except Exception as e:
        logger.error(f"Failed to forward message from {user_id}: {e}")
        # REQ: Error shows to sender if not delivered
        await message.reply("❌ Failed to send your message to support. Please try again later.")


@dp.message(F.chat.id == ADMIN_GROUP_ID, F.message_thread_id)
async def handle_admin_reply(message: types.Message):
    """
    Handles messages sent by admins INSIDE a specific forum topic.
    Finds the corresponding user and forwards the reply.
    """
    topic_id = message.message_thread_id

    # Ensure this isn't the "General" topic (id=0 or sometimes None depending on context, F.message_thread_id filters None)
    if topic_id == 0:
        return

    # Find out which user belongs to this topic
    target_user_id = await get_user_by_topic(topic_id)

    if not target_user_id:
        # This happens if admins talk in a topic created before this DB was set up
        # or if the DB was wiped.
        await message.reply("⚠️ Error: Cannot find the user associated with this topic in the database.")
        return

    try:
        # Forward the admin's reply back to the user
        await message.copy_to(chat_id=target_user_id)
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        # REQ: Error shows to admins if msg isn't received by user
        logger.warning(f"Failed to send reply to user {target_user_id}: {e}")
        await message.reply(f"❌ Could not deliver reply to user. They may have blocked the bot.\nError: {e.message}")

# --- MAIN EXECUTION ---
async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is not set!")
        exit(1)
        
    # Initialize DB before starting polling
    await init_db()
    
    logger.info("Bot starting polling...")
    # Drop pending updates so it doesn't process old messages on restart
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
