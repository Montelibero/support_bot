import os
from asyncio import sleep

from aiogram import types, Router, Bot, F
from aiogram.enums import ChatType, ChatMemberStatus, MessageEntityType, ContentType
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import ReactionTypeEmoji, ChatMemberUpdated, Message
from loguru import logger

from config.bot_config import SupportBotSettings, BotConfig
from database.repositories import Repo
from bot.customizations import get_customization, get_all_routers

router = Router()
router.include_router(get_all_routers())





class LinkChatCallbackData(CallbackData, prefix="link_chat"):
    old_chat_id: int
    new_chat_id: int
    new_thread_id: int | None
    action: str


@router.message(Command(commands=["start"]))
async def cmd_start(message: types.Message, bot: Bot, bot_settings: SupportBotSettings):
    await message.answer(text=bot_settings.start_message)


@router.message(Command(commands=["security_policy"]))
async def cmd_security_policy(message: types.Message, bot: Bot, bot_settings: SupportBotSettings):
    await message.answer(text=bot_settings.security_policy)


@router.message(Command(commands=["myname"]))
async def cmd_myname(message: types.Message, bot: Bot, repo: Repo, bot_settings: SupportBotSettings):
    if message.chat.id == bot_settings.master_chat:
        if bot_settings.ignore_commands:
            return
        else:
            data = message.text.strip().split(' ')
            if len(data) < 2:
                await message.answer(text='Необходимо указать имя после команды')
            else:
                username = ' '.join(data[1:])
                if username in await repo.get_all_users():
                    await message.answer(text=f'Псевдоним {username} уже занят')
                else:
                    await repo.save_user_name(user_id=message.from_user.id, user_name=username, bot_id=bot.id)
                    await message.answer(text=f'Имя сохранено как "{username}"')


@router.message(Command(commands=["show_names"]))
async def cmd_show_names(message: types.Message, bot: Bot, repo: Repo, bot_settings: SupportBotSettings):
    if message.chat.id == bot_settings.master_chat:
        if bot_settings.ignore_commands:
            return
        else:
            await message.answer(text=' '.join(await repo.get_all_users()))


@router.message(Command(commands=["ignore"]))
async def cmd_add_ignore(message: types.Message, bot: Bot, bot_settings: SupportBotSettings, config: BotConfig):
    if message.chat.id != bot_settings.master_chat:
        return

    # add or remove id to bot_settings.ignore_users
    data = message.text.strip().split(' ')
    if len(data) < 2:
        ignored_list = bot_settings.ignore_users[-5:]
        text = 'Необходимо указать ID пользователя после команды\n'
        if ignored_list:
            text += f'Всего в игноре: {len(bot_settings.ignore_users)}\n'
            text += f'Последние 5: {", ".join(map(str, ignored_list))}'
        else:
            text += 'Список игнорируемых пуст'
        await message.answer(text=text)
    else:
        try:
            user_id = int(data[1])
            if user_id in bot_settings.ignore_users:
                bot_settings.ignore_users.remove(user_id)
                await message.answer(text=f'ID {user_id} удален из игнорируемых')
            else:
                bot_settings.ignore_users.append(user_id)
                await message.answer(text=f'ID {user_id} добавлен в игнорируемые')
            await config.update_bot_setting(bot_settings)
        except ValueError:
            await message.answer(text='ID пользователя должен быть числом')


@router.message(Command(commands=["send"]))
async def cmd_send(message: types.Message, bot: Bot, repo: Repo, bot_settings: SupportBotSettings, config: BotConfig):
    if message.chat.id == bot_settings.master_chat:
        if bot_settings.ignore_commands:
            return
        if message.reply_to_message:
            all_users = message.text.split()
            good_users = []
            bad_users = []

            user_info = await repo.get_user_info(message.from_user.id)
            if user_info is None:
                await message.reply(
                    'Сообщение не отправлено. Не найден ваш псевдоним, пришлите "/myname псевдоним" '
                    'и повторите ваш ответ. /show_names покажет занятые псевдонимы')
                return
            i = 0
            for user in all_users:
                user = str(user)
                if len(user) > 5:  # user.upper().find('ID') != -1:
                    if user.upper().find('ID') > -1:
                        chat_id = int(user[user.upper().find('ID') + 2:])
                    else:
                        chat_id = int(user)
                    if i == 10:
                        await sleep(2)
                        i = 0
                    try:
                        i += 1
                        await resend_message_plus(message=message, bot=bot, repo=repo, chat_id=chat_id,
                                                  text=f'{message.reply_to_message.html_text}\n\n'
                                                       f'Вам ответил {user_info.user_name}',
                                                  reply_to_message_id=None, support_user_id=user_info.user_id,
                                                  message_thread_id=None, config=config, do_exception=True)
                        good_users.append(str(chat_id))
                    except Exception as ex:
                        bad_users.append(str(chat_id))
                        logger.info(ex)
                        pass
            await message.reply(f'was send to {" ".join(good_users)} \n can`t send to {" ".join(bad_users)}')
        else:
            await message.reply('Надо в ответ на сообщение')


async def cmd_send_file(message: types.Message, bot: Bot, filename):
    if os.path.isfile(filename):
        await bot.send_document(message.chat.id, types.FSInputFile(filename))


@router.message(Command(commands=["log"]))
async def cmd_log(message: types.Message, bot: Bot, config: BotConfig):
    if message.from_user.id == config.ADMIN_ID and message.chat.type == 'private':
        await cmd_send_file(message, bot, 'SupportBot.log')


@router.message(Command(commands=["err"]))
async def cmd_err(message: types.Message, bot: Bot, config: BotConfig):
    if message.from_user.id == config.ADMIN_ID and message.chat.type == 'private':
        await cmd_send_file(message, bot, 'SupportBot.err')


@router.message(Command(commands=["stats"]))
async def cmd_stats(message: types.Message, bot: Bot, repo: Repo, bot_settings: SupportBotSettings):
    if message.chat.id == bot_settings.master_chat:
        data = await repo.get_stats(bot_id=bot.id, master_chat_id=bot_settings.master_chat)
        await message.reply(text='\n'.join(data))


@router.message(Command(commands=["link"]))
@router.message(Command(commands=["link"]))
async def cmd_link(message: types.Message, bot: Bot, bot_settings: SupportBotSettings):
    if message.from_user.id == bot_settings.owner:
        thread_id = message.message_thread_id if message.is_topic_message else None
        thread_info = f" (topic ID: {thread_id})" if thread_id else ""

        buttons = [
            [
                types.InlineKeyboardButton(
                    text="Да",
                    callback_data=LinkChatCallbackData(
                        new_chat_id=message.chat.id,
                        old_chat_id=bot_settings.master_chat,
                        new_thread_id=thread_id,
                        action="yes"
                    ).pack()
                ),
                types.InlineKeyboardButton(
                    text="Нет",
                    callback_data=LinkChatCallbackData(
                        new_chat_id=message.chat.id,
                        old_chat_id=bot_settings.master_chat,
                        new_thread_id=thread_id,
                        action="no"
                    ).pack()
                )
            ]
        ]
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.reply(
            f"Do you want to link this chat (ID: {message.chat.id}){thread_info} "
            f"to master chat (ID: {bot_settings.master_chat})?",
            reply_markup=keyboard
        )
    else:
        await message.reply('Only the owner can use this command')


@router.message()
async def cmd_resend(message: types.Message, bot: Bot, repo: Repo, bot_settings: SupportBotSettings, config: BotConfig):
    logger.info(f"Support bot message - Username: {(await bot.me()).username}, Chat ID: {message.chat.id}")
    if message.chat.id == bot_settings.master_chat:
        if message.reply_to_message and message.reply_to_message.from_user.id == bot.id:
            user_info = await repo.get_user_info(message.from_user.id)
            if user_info is None:
                await message.reply('Сообщение не отправлено. Не найден ваш псевдоним, пришлите "/myname псевдоним" '
                                    'и повторите ваш ответ. /show_names покажет занятые псевдонимы')
                return
            resend_info = await repo.get_message_resend_info(bot_id=bot.id, resend_id=message.reply_to_message.message_id,
                                                  chat_for_id=message.chat.id)
            if resend_info is None:
                await message.reply('Сообщение не отправлено. Не найдена история переписки в БД \n'
                                    'Если вы уверены что переписка была, то пришлите /send ID123 '
                                    'в ответ на прошлое сообщение\n'
                                    'где 123 ID пользователя и сообщение будет ему отправлено.')
                return
            await resend_message_plus(message=message, bot=bot, repo=repo, chat_id=resend_info.chat_from_id,
                                      text=f'{message.html_text}\n\nВам ответил {user_info.user_name}',
                                      reply_to_message_id=resend_info.message_id, support_user_id=user_info.user_id,
                                      message_thread_id=None, config=config)
        else:
            await cmd_alert_bad(message, bot, bot_settings)
    elif message.chat.type == 'private':
        user_has_reply = await repo.has_user_received_reply(bot_id=bot.id, user_id=message.from_user.id)
        if not user_has_reply:
            if message.content_type != ContentType.TEXT:
                await message.reply("Ссылки и медиа запрещены / Links and media are not allowed")
                return
            if bot_settings.block_links and message.entities:
                for entity in message.entities:
                    if entity.type in [MessageEntityType.URL, MessageEntityType.TEXT_LINK,
                                       MessageEntityType.TEXT_MENTION, MessageEntityType.MENTION,
                                       MessageEntityType.HASHTAG, MessageEntityType.CASHTAG]:
                        await message.reply("Ссылки и медиа запрещены / Links and media are not allowed")
                        return
        if message.from_user.id in bot_settings.ignore_users:
            return

        user = message.from_user
        username = '@' + user.username if user.username else user.mention_html()
        reply_to_message_id = None

        if message.reply_to_message:
            resend_info = await repo.get_message_resend_info(bot_id=bot.id, resend_id=message.reply_to_message.message_id,
                                                  chat_for_id=message.chat.id)
            if resend_info:
                reply_to_message_id = resend_info.message_id

        # Use customization registry to get bot-specific extras
        customization = get_customization(bot.id)
        add_text = await customization.get_extra_text(user, message, bot_settings)
        reply_markup = await customization.get_reply_markup(user, message, bot_settings)

        text = f'{message.html_text}\n\n#ID{user.id} | {user.full_name} | {username}{add_text}'
        if bot_settings.use_auto_reply:
            text += '\n\n отправлен автоответ 🤖'

        await resend_message_plus(message=message, bot=bot, repo=repo, chat_id=bot_settings.master_chat,
                                  text=text,
                                  reply_to_message_id=reply_to_message_id, support_user_id=None,
                                  message_thread_id=bot_settings.master_thread, config=config,
                                  reply_markup=reply_markup)

        if bot_settings.use_auto_reply:
            await message.reply(bot_settings.auto_reply, disable_web_page_preview=True)


async def cmd_alert_bad(message: types.Message, bot: Bot, bot_settings: SupportBotSettings):
    if bot_settings.mark_bad:
        await message.react([ReactionTypeEmoji(emoji="🙈")])


@router.edited_message()
async def cmd_edit_msg(message: types.Message, bot: Bot, repo: Repo, bot_settings: SupportBotSettings, config: BotConfig):
    if message.chat.id == bot_settings.master_chat:
        if message.reply_to_message and message.reply_to_message.from_user.id == bot.id:
            user_info = await repo.get_user_info(message.from_user.id)
            if user_info is None:
                await message.reply('Сообщение не отправлено. Не найден ваш псевдоним, пришлите "/myname псевдоним" '
                                    'и повторите редактирование. /show_names покажет занятые псевдонимы')
                return
            send_info = await repo.get_message_resend_info(bot_id=bot.id, message_id=message.message_id,
                                                chat_from_id=message.chat.id)
            if send_info is None:
                await message.reply('Не удалось отправить изменения =(')
                return

            text = f'{message.html_text}\n\nВам ответил {user_info.user_name}'

            try:
                await bot.edit_message_text(chat_id=send_info.chat_for_id, text=text, message_id=send_info.resend_id)
                await message.reply("Изменение отправлено")
            except Exception as ex:
                if str(ex).find('Bad Request: message is not modified') > 0:
                    pass
                else:
                    await message.reply(f"Не получилось изменить сообщение =(\n{ex}")

    else:
        send_info = await repo.get_message_resend_info(bot_id=bot.id, message_id=message.message_id, chat_from_id=message.chat.id)
        if send_info:
            reply_to_message_id = send_info.resend_id
        else:
            await message.reply('Не удалось отправить изменения =(')
            return
        user = message.from_user
        username = '@' + user.username if user.username else user.mention_html()

        await resend_message_plus(message=message, bot=bot, repo=repo, chat_id=bot_settings.master_chat,
                                  text=f'{message.html_text}\n*** отредактировано ***\n'
                                       f'#ID{user.id} | {user.full_name} | {username}',
                                  reply_to_message_id=reply_to_message_id, support_user_id=None,
                                  message_thread_id=bot_settings.master_thread, config=config)


async def resend_message_plus(message: types.Message, bot: Bot, repo: Repo, chat_id: int, text: str, reply_to_message_id,
                              support_user_id, message_thread_id, config: BotConfig, do_exception=False,
                              reply_markup: types.InlineKeyboardMarkup = None):
    try:
        if message.photo:
            if message.media_group_id:
                if message.media_group_id in config.media_groups:
                    config.media_groups[message.media_group_id].append(message.photo[-1].file_id)
                    return
                config.media_groups[message.media_group_id] = [message.photo[-1].file_id]
                await sleep(7)

                new_album = [types.InputMediaPhoto(media=file_id) for file_id in
                             config.media_groups[message.media_group_id]]
                config.media_groups[message.media_group_id] = None
                resend_messages = await bot.send_media_group(chat_id=chat_id, message_thread_id=message_thread_id,
                                                             media=new_album,
                                                             reply_to_message_id=reply_to_message_id)
                for resend_message in resend_messages:
                    await repo.save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                                     resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                                     chat_for_id=resend_message.chat.id)
            else:
                resend_message = await bot.send_photo(chat_id=chat_id, message_thread_id=message_thread_id,
                                                      photo=message.photo[-1].file_id,
                                                      reply_to_message_id=reply_to_message_id)
                await repo.save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                                 resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                                 chat_for_id=resend_message.chat.id)

        if message.document:
            resend_message = await bot.send_document(chat_id=chat_id, message_thread_id=message_thread_id,
                                                     document=message.document.file_id,
                                                     reply_to_message_id=reply_to_message_id)
            await repo.save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                             resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                             chat_for_id=resend_message.chat.id)
        if message.sticker:
            resend_message = await bot.send_sticker(chat_id=chat_id, message_thread_id=message_thread_id,
                                                    sticker=message.sticker.file_id,
                                                    reply_to_message_id=reply_to_message_id)
            await repo.save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                             resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                             chat_for_id=resend_message.chat.id)
        if message.audio:
            resend_message = await bot.send_audio(chat_id=chat_id, message_thread_id=message_thread_id,
                                                  audio=message.audio.file_id,
                                                  reply_to_message_id=reply_to_message_id)
            await repo.save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                             resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                             chat_for_id=resend_message.chat.id)
        if message.video:
            resend_message = await bot.send_video(chat_id=chat_id, message_thread_id=message_thread_id,
                                                  video=message.video.file_id,
                                                  reply_to_message_id=reply_to_message_id)
            await repo.save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                             resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                             chat_for_id=resend_message.chat.id)
        if message.voice:
            resend_message = await bot.send_voice(chat_id=chat_id, message_thread_id=message_thread_id,
                                                  voice=message.voice.file_id,
                                                  reply_to_message_id=reply_to_message_id)
            await repo.save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                             resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                             chat_for_id=resend_message.chat.id)

        if message.video_note:
            resend_message = await bot.send_video_note(chat_id=chat_id, message_thread_id=message_thread_id,
                                                       video_note=message.video_note.file_id,
                                                       reply_to_message_id=reply_to_message_id)
            await repo.save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                             resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                             chat_for_id=resend_message.chat.id)

        if message.animation:
            resend_message = await bot.send_animation(chat_id=chat_id, message_thread_id=message_thread_id,
                                                      animation=message.animation.file_id,
                                                      reply_to_message_id=reply_to_message_id)
            save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                             resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                             chat_for_id=resend_message.chat.id)

        if message.location:
            resend_message = await bot.send_location(chat_id=chat_id, message_thread_id=message_thread_id,
                                                     latitude=message.location.latitude,
                                                     longitude=message.location.longitude,
                                                     reply_to_message_id=reply_to_message_id)
            save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                             resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                             chat_for_id=resend_message.chat.id)

        if message.contact:
            resend_message = await bot.send_contact(chat_id=chat_id, message_thread_id=message_thread_id,
                                                    phone_number=message.contact.phone_number,
                                                    first_name=message.contact.first_name,
                                                    last_name=message.contact.last_name,
                                                    reply_to_message_id=reply_to_message_id)
            save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                             resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                             chat_for_id=resend_message.chat.id)
        if message.venue:
            resend_message = await bot.send_venue(chat_id=chat_id, message_thread_id=message_thread_id,
                                                  latitude=message.venue.location.latitude,
                                                  longitude=message.venue.location.longitude,
                                                  title=message.venue.title,
                                                  address=message.venue.address,
                                                  reply_to_message_id=reply_to_message_id)
            save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                             resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                             chat_for_id=resend_message.chat.id)

        resend_message = await bot.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id,
                                                reply_to_message_id=reply_to_message_id, reply_markup=reply_markup)
        await repo.save_message_ids(bot_id=bot.id, user_id=support_user_id, message_id=message.message_id,
                         resend_id=resend_message.message_id, chat_from_id=message.chat.id,
                         chat_for_id=resend_message.chat.id)

    except Exception as ex:
        if message.chat.id == config.get_bot_setting(bot.id).master_chat:
            if do_exception:
                raise ex
            else:
                await message.answer(f'Ошибка отправки\n{ex}')
        else:
            await message.answer(f'Send error =(')





@router.message_reaction()
async def message_reaction(message: types.MessageReactionUpdated, bot: Bot, repo: Repo, bot_settings: SupportBotSettings):
    if len(message.new_reaction) == 0:
        return

    if message.chat.id == bot_settings.master_chat:
        send_info = await repo.get_message_resend_info(bot_id=bot.id, resend_id=message.message_id,
                                            chat_for_id=message.chat.id)
        if send_info is None:
            if bot_settings.mark_bad:
                await bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id,
                                               reaction=[ReactionTypeEmoji(emoji='👀')])
            return

        try:
            await bot.set_message_reaction(chat_id=send_info.chat_from_id, message_id=send_info.message_id,
                                           reaction=[message.new_reaction[0]])
            await bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id,
                                           reaction=[ReactionTypeEmoji(emoji='👍')])
        except Exception as ex:
            if str(ex).find('Bad Request: message is not modified') > 0:
                pass
            else:
                logger.error(ex)

    else:
        send_info = await repo.get_message_resend_info(bot_id=bot.id, resend_id=message.message_id,
                                            chat_for_id=message.chat.id)
        if send_info:
            try:
                await bot.set_message_reaction(chat_id=send_info.chat_from_id, message_id=send_info.message_id,
                                               reaction=[message.new_reaction[0]])
                await bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id,
                                               reaction=[ReactionTypeEmoji(emoji='👍')])
            except Exception as ex:
                if str(ex).find('Bad Request: message is not modified') > 0:
                    pass
                else:
                    logger.error(ex)


@router.my_chat_member()
async def on_my_chat_member(update: ChatMemberUpdated, bot: Bot):
    chat = update.chat
    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status
    bot_user = await bot.me()
    bot_info = f"Bot {bot_user.id} (@{bot_user.username})"

    if old_status != new_status:
        if new_status == ChatMemberStatus.MEMBER:
            logger.info(f"{bot_info} was added to chat {chat.id}")
            if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                await bot.send_message(chat.id,
                                       "Thanks for adding me to this chat! Please make me an admin to work properly.")

        elif new_status == ChatMemberStatus.LEFT:
            logger.info(f"{bot_info} was removed from chat {chat.id}")

        elif new_status == ChatMemberStatus.ADMINISTRATOR:
            logger.info(f"{bot_info} permissions were updated in chat {chat.id}")
            if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                await bot.send_message(chat.id,
                                       "Thanks for making me an admin! To link this chat, run the /link command in the topic you want to link.")

        elif new_status == ChatMemberStatus.RESTRICTED:
            logger.warning(f"{bot_info} permissions were restricted in chat {chat.id}")

        elif new_status == ChatMemberStatus.KICKED:
            logger.warning(f"{bot_info} was kicked from chat {chat.id}")

        else:
            logger.info(f"{bot_info} status changed in chat {chat.id} from {old_status} to {new_status}")


@router.callback_query(LinkChatCallbackData.filter())
async def process_link_callback(callback: types.CallbackQuery, callback_data: LinkChatCallbackData,
                                bot_settings: SupportBotSettings, config: BotConfig):
    if callback.from_user.id != bot_settings.owner:
        await callback.answer("Only the owner can use this command", show_alert=True)
        return

    if callback_data.action == "no":
        await callback.message.delete()
        await callback.answer("Operation cancelled")
    else:
        # Here you would implement the logic to save the link in your database
        # For example: save_chat_link(from_chat_id, to_chat_id, thread_id)
        bot_settings.master_chat = callback_data.new_chat_id
        bot_settings.master_thread = callback_data.new_thread_id
        bot_settings.can_work = False
        await config.update_bot_setting(bot_settings)
        await callback.message.edit_text(
            "Chat successfully linked!\n"
            "Now bot deactivated. "
            "You need to go to the bot admin panel and enable it.",
            reply_markup=None
        )
        await callback.answer("Settings saved")


@router.message(F.migrate_to_chat_id)
async def on_migrate(message: Message, bot: Bot, bot_settings: SupportBotSettings, config: BotConfig):
    old_chat_id = message.chat.id
    new_chat_id = message.migrate_to_chat_id
    logger.info(f"Chat {old_chat_id} migrated to {new_chat_id}")

    bot_settings.can_work = False
    await config.update_bot_setting(bot_settings)

    await message.bot.send_message(chat_id=new_chat_id,
                                   text=f"Chat {old_chat_id} migrated to {new_chat_id}\n"
                                        f"Bot was stopped. You need relink bot to this chat. "
                                        f"Use /link command in the desired topic of this chat.")
