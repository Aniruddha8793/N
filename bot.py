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
BOT_TOKEN = "8115043032:AAFDDtZrSZkLqZfNVKD4HYBoEttA7ZLWYMo"
ADMIN_GROUP_ID = -1003570824341

# We save the DB file to a specific path. 
# On Railway, files are deleted on every "deploy" unless you use a Volume.
# For now, this saves it in the current folder.
DATABASE_NAME = "/app/data/modmail.db"

# --- SETUP ---
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- DATABASE FUNCTIONS (SQLite) ---

async def init_db():
    """Creates the table if it doesn't exist."""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS user_topics (user_id INTEGER PRIMARY KEY, topic_id INTEGER)"
        )
        await db.commit()

async def get_topic_by_user(user_id: int) -> int | None:
    """Finds the topic ID for a specific user."""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        async with db.execute("SELECT topic_id FROM user_topics WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def get_user_by_topic(topic_id: int) -> int | None:
    """Finds the user ID associated with a specific topic."""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        async with db.execute("SELECT user_id FROM user_topics WHERE topic_id = ?", (topic_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def save_user_topic(user_id: int, topic_id: int):
    """Saves the link between a user and a topic."""
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO user_topics (user_id, topic_id) VALUES (?, ?)", (user_id, topic_id))
        await db.commit()


# --- HANDLERS ---

@dp.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def command_start_handler(message: types.Message) -> None:
    await message.answer(f"Hello, {hbold(message.from_user.full_name)}! Any message you send here will be forwarded to the admins.", parse_mode=ParseMode.HTML)

@dp.message(F.chat.type == ChatType.PRIVATE)
async def handle_user_message(message: types.Message):
    """
    Handles USER messages: Check DB for topic, create if new, forward message.
    """
    user_id = message.from_user.id
    user = message.from_user

    # 1. Check Database for existing topic
    topic_id = await get_topic_by_user(user_id)

    # 2. If new user, create topic and save to DB
    if topic_id is None:
        try:
            topic_name = f"{user.full_name} [{user_id}]"[:127]
            
            logger.info(f"Creating new topic for user {user_id}")
            new_topic = await bot.create_forum_topic(chat_id=ADMIN_GROUP_ID, name=topic_name)
            topic_id = new_topic.message_thread_id

            await save_user_topic(user_id, topic_id)

            # Send Info Message to the new topic
            username_txt = f"@{user.username}" if user.username else "None"
            info_text = (
                f"🆕 **New Ticket Created**\n\n"
                f"👤 **User:** {hbold(user.full_name)}\n"
                f"🆔 **ID:** {hcode(user_id)}\n"
                f"🔗 **Username:** {username_txt}\n"
                f"🗣 **Language:** {user.language_code}"
            )
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                message_thread_id=topic_id,
                text=info_text,
                parse_mode=ParseMode.HTML
            )
            
        except TelegramBadRequest as e:
            logger.error(f"Failed to create topic: {e}")
            await message.reply("⚠️ Error: Could not connect to support. Ensure bot is Admin and Topics are enabled.")
            return

    # 3. Forward the message content (text, image, poll, etc)
    try:
        await message.copy_to(chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id)
    except Exception as e:
        logger.error(f"Failed to forward message from {user_id}: {e}")
        await message.reply("❌ Failed to send your message to support. Please try again later.")


@dp.message(F.chat.id == ADMIN_GROUP_ID, F.message_thread_id)
async def handle_admin_reply(message: types.Message):
    """
    Handles ADMIN messages: Find user from DB, copy message to them.
    """
    topic_id = message.message_thread_id

    # Ignore General topic
    if not topic_id:
        return

    # 1. Find user from Database
    target_user_id = await get_user_by_topic(topic_id)

    if not target_user_id:
        await message.reply("⚠️ Error: Cannot find the user associated with this topic in the database.")
        return

    # 2. Send to user
    try:
        await message.copy_to(chat_id=target_user_id)
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        logger.warning(f"Failed to send reply to user {target_user_id}: {e}")
        await message.reply(f"❌ Could not deliver reply to user. They may have blocked the bot.\nError: {e.message}")

# --- MAIN ---
async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set!")
        exit(1)
        
    await init_db()
    
    logger.info("Bot starting polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
