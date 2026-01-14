from aiogram import Router, Bot, F
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ChatMemberUpdated, CallbackQuery
from aiogram_dialog import DialogManager, StartMode
from loguru import logger

from bot.routers.admin_dialog import AdminBotStates

router = Router()


@router.message(CommandStart())
async def cmd_admin_start(message: Message, dialog_manager: DialogManager):
    await dialog_manager.start(AdminBotStates.main, mode=StartMode.RESET_STACK)


@router.message(Command(commands=["my_bots"]))
async def cmd_admin_my_bots(message: Message, dialog_manager: DialogManager):
    await dialog_manager.start(AdminBotStates.main, mode=StartMode.RESET_STACK)


@router.message(Command(commands=["add_bot"]))
async def cmd_admin_add_bot(message: Message, dialog_manager: DialogManager):
    await dialog_manager.start(AdminBotStates.token, mode=StartMode.NEW_STACK)


# @router.message(Command(commands=["exit"]))
# @router.message(Command(commands=["restart"]))
# async def cmd_exit(message: types.Message, bot: Bot):
#     if message.from_user.username == "itolstov":
#         if bot_setting[bot.id].get('need_reboot'):
#             await message.reply("Chao :[[[")
#             exit()
#         else:
#             bot_setting[bot.id]['need_reboot'] = True
#             await message.reply(":'[")


@router.my_chat_member()
async def on_my_chat_member(update: ChatMemberUpdated, bot: Bot):
    chat = update.chat
    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status

    if old_status != new_status:
        if new_status == ChatMemberStatus.MEMBER:
            logger.info(f"Bot was added to chat {chat.id}")

        elif new_status == ChatMemberStatus.LEFT:
            logger.info(f"Bot was removed from chat {chat.id}")

        elif new_status == ChatMemberStatus.ADMINISTRATOR:
            logger.info(f"Bot's permissions were updated in chat {chat.id}")

        elif new_status == ChatMemberStatus.RESTRICTED:
            logger.warning(f"Bot's permissions were restricted in chat {chat.id}")

        elif new_status == ChatMemberStatus.KICKED:
            logger.warning(f"Bot's permissions were kicked in chat {chat.id}")

        else:
            logger.info(f"Bot status changed in chat {chat.id} from {old_status} to {new_status}")


@router.message(F.migrate_to_chat_id)
async def on_migrate(message: Message, bot: Bot):
    old_chat_id = message.chat.id
    new_chat_id = message.migrate_to_chat_id
    logger.info(f"Chat {old_chat_id} migrated to {new_chat_id}")
    await message.bot.send_message(chat_id=new_chat_id,
                                   text=f"Chat {old_chat_id} migrated to {new_chat_id}")

@router.callback_query(F.data == "test")
async def on_start(callback_query: CallbackQuery, bot: Bot):
    #need to resolve callback_query
    await callback_query.answer("Hello, world!")
