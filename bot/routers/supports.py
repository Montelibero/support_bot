import os
from html import escape
from asyncio import sleep

from aiogram import types, Router, Bot, F
from aiogram.enums import ChatType, ChatMemberStatus, MessageEntityType, ContentType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    ReactionTypeEmoji,
    ChatMemberUpdated,
    Message,
    MessageOriginUser,
    MessageOriginHiddenUser,
    MessageOriginChat,
    MessageOriginChannel,
    MediaUnion,
)
from loguru import logger

from config.bot_config import SupportBotSettings, BotConfig
from database.repositories import Repo
from bot.customizations import get_customization, get_all_routers
from bot.reactions import safe_react_to_message, safe_set_message_reaction

router = Router()
router.include_router(get_all_routers())


def _require_text(message: types.Message) -> str:
    assert message.text is not None
    return message.text


def _require_from_user(message: types.Message) -> types.User:
    assert message.from_user is not None
    return message.from_user


def _require_master_chat(bot_settings: SupportBotSettings) -> int:
    assert bot_settings.master_chat is not None
    return bot_settings.master_chat


def _format_forwarded_source(message: types.Message) -> str | None:
    origin = message.forward_origin

    if isinstance(origin, MessageOriginUser):
        return f"пользователь {origin.sender_user.full_name}"
    if isinstance(origin, MessageOriginHiddenUser):
        return f"скрытый отправитель {origin.sender_user_name}"
    if isinstance(origin, MessageOriginChat):
        return f"чат {origin.sender_chat.title}"
    if isinstance(origin, MessageOriginChannel):
        return f"канал {origin.chat.title}"

    if message.forward_from is not None:
        return f"пользователь {message.forward_from.full_name}"
    if message.forward_sender_name:
        return f"скрытый отправитель {message.forward_sender_name}"
    if message.forward_from_chat is not None:
        chat_type = "канал" if message.forward_from_chat.type == "channel" else "чат"
        return f"{chat_type} {message.forward_from_chat.title}"
    if message.is_automatic_forward:
        return "автопересылка"

    return None


def _build_forwarded_prefix(message: types.Message) -> str:
    source = _format_forwarded_source(message)
    if source is None:
        return ""

    return f"<b>Пересланное сообщение</b>\nИсточник: {escape(source)}\n\n"


def _build_master_chat_text(
    message: types.Message, user: types.User, add_text: str, *, edited: bool = False
) -> str:
    username = "@" + user.username if user.username else user.mention_html()
    prefix = _build_forwarded_prefix(message)
    edited_marker = "\n*** отредактировано ***" if edited else ""
    return (
        f"{prefix}{message.html_text}{edited_marker}\n"
        f"#ID{user.id} | {user.full_name} | {username}{add_text}"
    )


def _resolve_agent_name(
    user_id: int,
    bot_settings: SupportBotSettings,
    user_info: object | None,
) -> str | None:
    """Return agent display name or None if not set."""
    if bot_settings.use_local_names:
        return bot_settings.local_names.get(str(user_id))
    if user_info is not None:
        return user_info.user_name  # type: ignore[union-attr]
    return None


def _no_name_error_text(use_local_names: bool) -> str:
    """Build error message when agent has no pseudonym."""
    mode = (
        "включены локальные имена"
        if use_local_names
        else "используются глобальные имена"
    )
    return (
        f"Сообщение не отправлено. Не найден ваш псевдоним ({mode}), "
        f'пришлите "/myname псевдоним" и повторите ваш ответ. '
        f"/show_names покажет занятые псевдонимы"
    )


class LinkChatCallbackData(CallbackData, prefix="link_chat"):
    old_chat_id: int
    new_chat_id: int
    new_thread_id: int | None
    action: str


@router.message(Command(commands=["start"]))
async def cmd_start(message: types.Message, bot: Bot, bot_settings: SupportBotSettings):
    await message.answer(text=bot_settings.start_message)


@router.message(Command(commands=["security_policy"]))
async def cmd_security_policy(
    message: types.Message, bot: Bot, bot_settings: SupportBotSettings
):
    await message.answer(text=bot_settings.security_policy)


@router.message(Command(commands=["myname"]))
async def cmd_myname(
    message: types.Message,
    bot: Bot,
    repo: Repo,
    bot_settings: SupportBotSettings,
    config: BotConfig,
):
    if message.chat.id != bot_settings.master_chat:
        return
    if bot_settings.ignore_commands:
        return

    data = _require_text(message).strip().split(" ")
    user = _require_from_user(message)
    if len(data) < 2:
        await message.answer(text="Необходимо указать имя после команды")
        return

    username = " ".join(data[1:])

    if bot_settings.use_local_names:
        if username in bot_settings.local_names.values():
            await message.answer(text=f"Псевдоним {username} уже занят")
            return
        bot_settings.local_names[str(user.id)] = username
        await config.update_bot_setting(bot_settings)
        await message.answer(
            text=f'Имя сохранено как "{username}" (локально для этого бота)'
        )
    else:
        if username in await repo.get_all_users():
            await message.answer(text=f"Псевдоним {username} уже занят")
            return
        await repo.save_user_name(user_id=user.id, user_name=username, bot_id=bot.id)
        await message.answer(text=f'Имя сохранено как "{username}" (глобально)')


@router.message(Command(commands=["show_names"]))
async def cmd_show_names(
    message: types.Message, bot: Bot, repo: Repo, bot_settings: SupportBotSettings
):
    if message.chat.id != bot_settings.master_chat:
        return
    if bot_settings.ignore_commands:
        return

    if bot_settings.use_local_names:
        if bot_settings.local_names:
            names = " ".join(
                f"{name} (#ID{uid})" for uid, name in bot_settings.local_names.items()
            )
        else:
            names = "(пусто)"
        await message.answer(text=f"Локальные имена:\n{names}")
    else:
        all_users = await repo.get_all_users()
        label = "Глобальные имена:\n" if all_users else ""
        await message.answer(text=f"{label}{' '.join(all_users)}")


@router.message(Command(commands=["ignore"]))
async def cmd_add_ignore(
    message: types.Message,
    bot: Bot,
    bot_settings: SupportBotSettings,
    config: BotConfig,
):
    if message.chat.id != bot_settings.master_chat:
        return

    # add or remove id to bot_settings.ignore_users
    data = _require_text(message).strip().split(" ")
    if len(data) < 2:
        ignored_list = bot_settings.ignore_users[-5:]
        text = "Необходимо указать ID пользователя после команды\n"
        if ignored_list:
            text += f"Всего в игноре: {len(bot_settings.ignore_users)}\n"
            text += f"Последние 5: {', '.join(map(str, ignored_list))}"
        else:
            text += "Список игнорируемых пуст"
        await message.answer(text=text)
    else:
        try:
            user_id = int(data[1])
            if user_id in bot_settings.ignore_users:
                bot_settings.ignore_users.remove(user_id)
                await message.answer(text=f"ID {user_id} удален из игнорируемых")
            else:
                bot_settings.ignore_users.append(user_id)
                await message.answer(text=f"ID {user_id} добавлен в игнорируемые")
            await config.update_bot_setting(bot_settings)
        except ValueError:
            await message.answer(text="ID пользователя должен быть числом")


@router.message(Command(commands=["send"]))
async def cmd_send(
    message: types.Message,
    bot: Bot,
    repo: Repo,
    bot_settings: SupportBotSettings,
    config: BotConfig,
):
    if message.chat.id == bot_settings.master_chat:
        if bot_settings.ignore_commands:
            return
        if message.reply_to_message:
            all_users = _require_text(message).split()
            good_users = []
            bad_users = []
            from_user = _require_from_user(message)

            user_info = (
                None
                if bot_settings.use_local_names
                else await repo.get_user_info(from_user.id)
            )
            agent_name = _resolve_agent_name(from_user.id, bot_settings, user_info)
            if agent_name is None:
                await message.reply(_no_name_error_text(bot_settings.use_local_names))
                return
            support_user_id = (
                from_user.id if bot_settings.use_local_names else user_info.user_id  # type: ignore[union-attr]
            )
            i = 0
            for user in all_users:
                user = str(user)
                if len(user) > 5:  # user.upper().find('ID') != -1:
                    if user.upper().find("ID") > -1:
                        chat_id = int(user[user.upper().find("ID") + 2 :])
                    else:
                        chat_id = int(user)
                    if i == 10:
                        await sleep(2)
                        i = 0
                    try:
                        i += 1
                        await resend_message_plus(
                            message=message,
                            bot=bot,
                            repo=repo,
                            chat_id=chat_id,
                            text=f"{message.reply_to_message.html_text}\n\n"
                            f"Вам ответил {agent_name}",
                            reply_to_message_id=None,
                            support_user_id=support_user_id,
                            message_thread_id=None,
                            config=config,
                            do_exception=True,
                        )
                        good_users.append(str(chat_id))
                    except Exception as ex:
                        bad_users.append(str(chat_id))
                        logger.warning(
                            f"cmd_send failed — bot_id={bot.id}, target_chat_id={chat_id}: {ex}"
                        )
            await message.reply(
                f"was send to {' '.join(good_users)} \n can`t send to {' '.join(bad_users)}"
            )
        else:
            await message.reply("Надо в ответ на сообщение")


async def cmd_send_file(message: types.Message, bot: Bot, filename):
    if os.path.isfile(filename):
        await bot.send_document(message.chat.id, types.FSInputFile(filename))


@router.message(Command(commands=["log"]))
async def cmd_log(message: types.Message, bot: Bot, config: BotConfig):
    from_user = message.from_user
    if (
        from_user is not None
        and from_user.id == config.ADMIN_ID
        and message.chat.type == "private"
    ):
        await cmd_send_file(message, bot, "SupportBot.log")


@router.message(Command(commands=["err"]))
async def cmd_err(message: types.Message, bot: Bot, config: BotConfig):
    from_user = message.from_user
    if (
        from_user is not None
        and from_user.id == config.ADMIN_ID
        and message.chat.type == "private"
    ):
        await cmd_send_file(message, bot, "SupportBot.err")


@router.message(Command(commands=["stats"]))
async def cmd_stats(
    message: types.Message, bot: Bot, repo: Repo, bot_settings: SupportBotSettings
):
    master_chat = bot_settings.master_chat
    if message.chat.id == master_chat and master_chat is not None:
        agent_counts = await repo.get_agent_message_counts(
            bot_id=bot.id, master_chat_id=master_chat
        )
        total_messages = await repo.get_total_user_messages(
            bot_id=bot.id, master_chat_id=master_chat
        )

        result = []
        for user_id, count in agent_counts:
            if bot_settings.use_local_names:
                name = bot_settings.local_names.get(str(user_id))
            else:
                user_info = await repo.get_user_info(user_id)
                name = user_info.user_name if user_info else None
            display = name or f"#ID{user_id}"
            result.append(f"{display}: {count} messages")
        result.append(f"Total messages from users: {total_messages}")

        await message.reply(text="\n".join(result))


@router.message(Command(commands=["link"]))
@router.message(Command(commands=["link"]))
async def cmd_link(message: types.Message, bot: Bot, bot_settings: SupportBotSettings):
    from_user = message.from_user
    master_chat = bot_settings.master_chat
    if (
        from_user is not None
        and from_user.id == bot_settings.owner
        and master_chat is not None
    ):
        thread_id = message.message_thread_id if message.is_topic_message else None
        thread_info = f" (topic ID: {thread_id})" if thread_id else ""

        buttons = [
            [
                types.InlineKeyboardButton(
                    text="Да",
                    callback_data=LinkChatCallbackData(
                        new_chat_id=message.chat.id,
                        old_chat_id=master_chat,
                        new_thread_id=thread_id,
                        action="yes",
                    ).pack(),
                ),
                types.InlineKeyboardButton(
                    text="Нет",
                    callback_data=LinkChatCallbackData(
                        new_chat_id=message.chat.id,
                        old_chat_id=master_chat,
                        new_thread_id=thread_id,
                        action="no",
                    ).pack(),
                ),
            ]
        ]
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.reply(
            f"Do you want to link this chat (ID: {message.chat.id}){thread_info} "
            f"to master chat (ID: {master_chat})?",
            reply_markup=keyboard,
        )
    else:
        await message.reply("Only the owner can use this command")


@router.message()
async def cmd_resend(
    message: types.Message,
    bot: Bot,
    repo: Repo,
    bot_settings: SupportBotSettings,
    config: BotConfig,
):
    logger.info(
        f"Support bot message - Username: {(await bot.get_me()).username}, Chat ID: {message.chat.id}"
    )
    if message.chat.id == bot_settings.master_chat:
        reply_message = message.reply_to_message
        reply_user = reply_message.from_user if reply_message is not None else None
        if reply_message and reply_user is not None and reply_user.id == bot.id:
            from_user = _require_from_user(message)
            user_info = (
                None
                if bot_settings.use_local_names
                else await repo.get_user_info(from_user.id)
            )
            agent_name = _resolve_agent_name(from_user.id, bot_settings, user_info)
            if agent_name is None:
                await message.reply(_no_name_error_text(bot_settings.use_local_names))
                return
            support_user_id = (
                from_user.id if bot_settings.use_local_names else user_info.user_id  # type: ignore[union-attr]
            )
            resend_info = await repo.get_message_resend_info(
                bot_id=bot.id,
                resend_id=reply_message.message_id,
                chat_for_id=message.chat.id,
            )
            if resend_info is None:
                await message.reply(
                    "Сообщение не отправлено. Не найдена история переписки в БД \n"
                    "Если вы уверены что переписка была, то пришлите /send ID123 "
                    "в ответ на прошлое сообщение\n"
                    "где 123 ID пользователя и сообщение будет ему отправлено."
                )
                return
            await resend_message_plus(
                message=message,
                bot=bot,
                repo=repo,
                chat_id=resend_info.chat_from_id,
                text=f"{message.html_text}\n\nВам ответил {agent_name}",
                reply_to_message_id=resend_info.message_id,
                support_user_id=support_user_id,
                message_thread_id=None,
                config=config,
            )
        else:
            await cmd_alert_bad(message, bot, bot_settings)
    elif message.chat.type == "private":
        from_user = _require_from_user(message)
        master_chat = _require_master_chat(bot_settings)
        user_has_reply = await repo.has_user_received_reply(
            bot_id=bot.id, user_id=from_user.id
        )
        if not user_has_reply and bot_settings.block_links:
            if message.content_type != ContentType.TEXT:
                await message.reply(
                    "Ссылки и медиа запрещены / Links and media are not allowed"
                )
                return
            if message.entities:
                for entity in message.entities:
                    if entity.type in [
                        MessageEntityType.URL,
                        MessageEntityType.TEXT_LINK,
                        MessageEntityType.TEXT_MENTION,
                        MessageEntityType.MENTION,
                        MessageEntityType.HASHTAG,
                        MessageEntityType.CASHTAG,
                    ]:
                        await message.reply(
                            "Ссылки и медиа запрещены / Links and media are not allowed"
                        )
                        return
        if from_user.id in bot_settings.ignore_users:
            return

        user = from_user
        reply_to_message_id = None

        if message.reply_to_message:
            resend_info = await repo.get_message_resend_info(
                bot_id=bot.id,
                resend_id=message.reply_to_message.message_id,
                chat_for_id=message.chat.id,
            )
            if resend_info:
                reply_to_message_id = resend_info.message_id

        # Use customization registry to get bot-specific extras
        customization = get_customization(bot.id)
        add_text = await customization.get_extra_text(user, message, bot_settings)
        reply_markup = await customization.get_reply_markup(user, message, bot_settings)

        text = _build_master_chat_text(message, user, add_text)
        if bot_settings.use_auto_reply:
            text += "\n\n отправлен автоответ 🤖"

        await resend_message_plus(
            message=message,
            bot=bot,
            repo=repo,
            chat_id=master_chat,
            text=text,
            reply_to_message_id=reply_to_message_id,
            support_user_id=None,
            message_thread_id=bot_settings.master_thread,
            config=config,
            reply_markup=reply_markup,
        )

        if bot_settings.use_auto_reply:
            await message.reply(bot_settings.auto_reply, disable_web_page_preview=True)


async def cmd_alert_bad(
    message: types.Message, bot: Bot, bot_settings: SupportBotSettings
):
    if bot_settings.mark_bad:
        await safe_react_to_message(
            message,
            ReactionTypeEmoji(emoji="🙈"),
            log_hint="alert_bad",
        )


@router.edited_message()
async def cmd_edit_msg(
    message: types.Message,
    bot: Bot,
    repo: Repo,
    bot_settings: SupportBotSettings,
    config: BotConfig,
):
    if message.chat.id == bot_settings.master_chat:
        reply_message = message.reply_to_message
        reply_user = reply_message.from_user if reply_message is not None else None
        if reply_message and reply_user is not None and reply_user.id == bot.id:
            from_user = _require_from_user(message)
            user_info = (
                None
                if bot_settings.use_local_names
                else await repo.get_user_info(from_user.id)
            )
            agent_name = _resolve_agent_name(from_user.id, bot_settings, user_info)
            if agent_name is None:
                await message.reply(_no_name_error_text(bot_settings.use_local_names))
                return
            send_info = await repo.get_message_resend_info(
                bot_id=bot.id,
                message_id=message.message_id,
                chat_from_id=message.chat.id,
            )
            if send_info is None:
                await message.reply("Не удалось отправить изменения =(")
                return

            text = f"{message.html_text}\n\nВам ответил {agent_name}"

            try:
                await bot.edit_message_text(
                    chat_id=send_info.chat_for_id,
                    text=text,
                    message_id=send_info.resend_id,
                )
                await message.reply("Изменение отправлено")
            except Exception as ex:
                if str(ex).find("Bad Request: message is not modified") > 0:
                    pass
                else:
                    logger.warning(
                        f"edit_message_text failed — bot_id={bot.id}, "
                        f"chat_id={send_info.chat_for_id}, message_id={send_info.resend_id}: {ex}"
                    )
                    await message.reply(f"Не получилось изменить сообщение =(\n{ex}")

    else:
        user = _require_from_user(message)
        master_chat = _require_master_chat(bot_settings)
        send_info = await repo.get_message_resend_info(
            bot_id=bot.id, message_id=message.message_id, chat_from_id=message.chat.id
        )
        if send_info:
            reply_to_message_id = send_info.resend_id
        else:
            await message.reply("Не удалось отправить изменения =(")
            return

        await resend_message_plus(
            message=message,
            bot=bot,
            repo=repo,
            chat_id=master_chat,
            text=_build_master_chat_text(message, user, "", edited=True),
            reply_to_message_id=reply_to_message_id,
            support_user_id=None,
            message_thread_id=bot_settings.master_thread,
            config=config,
        )


async def resend_message_plus(
    message: types.Message,
    bot: Bot,
    repo: Repo,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None,
    support_user_id: int | None,
    message_thread_id: int | None,
    config: BotConfig,
    do_exception: bool = False,
    reply_markup: types.InlineKeyboardMarkup | None = None,
):
    try:
        if message.photo:
            if message.media_group_id:
                if message.media_group_id in config.media_groups:
                    config.media_groups[message.media_group_id].append(
                        message.photo[-1].file_id
                    )
                    return
                config.media_groups[message.media_group_id] = [
                    message.photo[-1].file_id
                ]
                await sleep(7)

                album_file_ids = config.media_groups.pop(message.media_group_id, [])
                new_album: list[MediaUnion] = [
                    types.InputMediaPhoto(media=file_id) for file_id in album_file_ids
                ]
                resend_messages = await bot.send_media_group(
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    media=new_album,
                    reply_to_message_id=reply_to_message_id,
                )
                for resend_message in resend_messages:
                    await repo.save_message_ids(
                        bot_id=bot.id,
                        user_id=support_user_id,
                        message_id=message.message_id,
                        resend_id=resend_message.message_id,
                        chat_from_id=message.chat.id,
                        chat_for_id=resend_message.chat.id,
                    )
            else:
                resend_message = await bot.send_photo(
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    photo=message.photo[-1].file_id,
                    reply_to_message_id=reply_to_message_id,
                )
                await repo.save_message_ids(
                    bot_id=bot.id,
                    user_id=support_user_id,
                    message_id=message.message_id,
                    resend_id=resend_message.message_id,
                    chat_from_id=message.chat.id,
                    chat_for_id=resend_message.chat.id,
                )

        if message.document:
            resend_message = await bot.send_document(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                document=message.document.file_id,
                reply_to_message_id=reply_to_message_id,
            )
            await repo.save_message_ids(
                bot_id=bot.id,
                user_id=support_user_id,
                message_id=message.message_id,
                resend_id=resend_message.message_id,
                chat_from_id=message.chat.id,
                chat_for_id=resend_message.chat.id,
            )
        if message.sticker:
            resend_message = await bot.send_sticker(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                sticker=message.sticker.file_id,
                reply_to_message_id=reply_to_message_id,
            )
            await repo.save_message_ids(
                bot_id=bot.id,
                user_id=support_user_id,
                message_id=message.message_id,
                resend_id=resend_message.message_id,
                chat_from_id=message.chat.id,
                chat_for_id=resend_message.chat.id,
            )
        if message.audio:
            resend_message = await bot.send_audio(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                audio=message.audio.file_id,
                reply_to_message_id=reply_to_message_id,
            )
            await repo.save_message_ids(
                bot_id=bot.id,
                user_id=support_user_id,
                message_id=message.message_id,
                resend_id=resend_message.message_id,
                chat_from_id=message.chat.id,
                chat_for_id=resend_message.chat.id,
            )
        if message.video:
            resend_message = await bot.send_video(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                video=message.video.file_id,
                reply_to_message_id=reply_to_message_id,
            )
            await repo.save_message_ids(
                bot_id=bot.id,
                user_id=support_user_id,
                message_id=message.message_id,
                resend_id=resend_message.message_id,
                chat_from_id=message.chat.id,
                chat_for_id=resend_message.chat.id,
            )
        if message.voice:
            resend_message = await bot.send_voice(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                voice=message.voice.file_id,
                reply_to_message_id=reply_to_message_id,
            )
            await repo.save_message_ids(
                bot_id=bot.id,
                user_id=support_user_id,
                message_id=message.message_id,
                resend_id=resend_message.message_id,
                chat_from_id=message.chat.id,
                chat_for_id=resend_message.chat.id,
            )

        if message.video_note:
            resend_message = await bot.send_video_note(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                video_note=message.video_note.file_id,
                reply_to_message_id=reply_to_message_id,
            )
            await repo.save_message_ids(
                bot_id=bot.id,
                user_id=support_user_id,
                message_id=message.message_id,
                resend_id=resend_message.message_id,
                chat_from_id=message.chat.id,
                chat_for_id=resend_message.chat.id,
            )

        if message.animation:
            resend_message = await bot.send_animation(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                animation=message.animation.file_id,
                reply_to_message_id=reply_to_message_id,
            )
            await repo.save_message_ids(
                bot_id=bot.id,
                user_id=support_user_id,
                message_id=message.message_id,
                resend_id=resend_message.message_id,
                chat_from_id=message.chat.id,
                chat_for_id=resend_message.chat.id,
            )

        if message.location:
            resend_message = await bot.send_location(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                latitude=message.location.latitude,
                longitude=message.location.longitude,
                reply_to_message_id=reply_to_message_id,
            )
            await repo.save_message_ids(
                bot_id=bot.id,
                user_id=support_user_id,
                message_id=message.message_id,
                resend_id=resend_message.message_id,
                chat_from_id=message.chat.id,
                chat_for_id=resend_message.chat.id,
            )

        if message.contact:
            resend_message = await bot.send_contact(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                phone_number=message.contact.phone_number,
                first_name=message.contact.first_name,
                last_name=message.contact.last_name,
                reply_to_message_id=reply_to_message_id,
            )
            await repo.save_message_ids(
                bot_id=bot.id,
                user_id=support_user_id,
                message_id=message.message_id,
                resend_id=resend_message.message_id,
                chat_from_id=message.chat.id,
                chat_for_id=resend_message.chat.id,
            )
        if message.venue:
            resend_message = await bot.send_venue(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                latitude=message.venue.location.latitude,
                longitude=message.venue.location.longitude,
                title=message.venue.title,
                address=message.venue.address,
                reply_to_message_id=reply_to_message_id,
            )
            await repo.save_message_ids(
                bot_id=bot.id,
                user_id=support_user_id,
                message_id=message.message_id,
                resend_id=resend_message.message_id,
                chat_from_id=message.chat.id,
                chat_for_id=resend_message.chat.id,
            )

        resend_message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            message_thread_id=message_thread_id,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
        )
        await repo.save_message_ids(
            bot_id=bot.id,
            user_id=support_user_id,
            message_id=message.message_id,
            resend_id=resend_message.message_id,
            chat_from_id=message.chat.id,
            chat_for_id=resend_message.chat.id,
        )

    except TelegramBadRequest as ex:
        if (
            "message reply" in str(ex).lower()
            or "message to be replied" in str(ex).lower()
            or "not found" in str(ex).lower()
        ):
            logger.warning(
                f"Message to reply not found or deleted, sending as new message: {ex}"
            )
            if reply_to_message_id is not None:
                await resend_message_plus(
                    message=message,
                    bot=bot,
                    repo=repo,
                    chat_id=chat_id,
                    text=text,
                    reply_to_message_id=None,
                    support_user_id=support_user_id,
                    message_thread_id=message_thread_id,
                    config=config,
                    do_exception=do_exception,
                    reply_markup=reply_markup,
                )
                return
        logger.error(
            f"resend_message_plus TelegramBadRequest — bot_id={bot.id}, "
            f"src_chat_id={message.chat.id}, dst_chat_id={chat_id}, "
            f"message_id={message.message_id}: {ex}"
        )
        current_settings = config.get_bot_setting(bot.id)
        if (
            current_settings is not None
            and message.chat.id == current_settings.master_chat
        ):
            if do_exception:
                raise ex
            else:
                await message.answer(f"Ошибка отправки\n{ex}")
        else:
            await message.answer("Send error =(")

    except Exception as ex:
        logger.error(
            f"resend_message_plus failed — bot_id={bot.id}, src_chat_id={message.chat.id}, "
            f"dst_chat_id={chat_id}, message_id={message.message_id}: {ex}"
        )
        current_settings = config.get_bot_setting(bot.id)
        if (
            current_settings is not None
            and message.chat.id == current_settings.master_chat
        ):
            if do_exception:
                raise ex
            else:
                await message.answer(f"Ошибка отправки\n{ex}")
        else:
            await message.answer("Send error =(")


@router.message_reaction()
async def message_reaction(
    message: types.MessageReactionUpdated,
    bot: Bot,
    repo: Repo,
    bot_settings: SupportBotSettings,
):
    if len(message.new_reaction) == 0:
        return

    if message.chat.id == bot_settings.master_chat:
        # Check if Admin is reacting to a forwarded ticket (resend_id=msg_id)
        send_info = await repo.get_message_resend_info(
            bot_id=bot.id, resend_id=message.message_id, chat_for_id=message.chat.id
        )
        # If not, check if Admin is reacting to their own reply (message_id=msg_id)
        if not send_info:
            send_info = await repo.get_message_resend_info(
                bot_id=bot.id,
                message_id=message.message_id,
                chat_from_id=message.chat.id,
            )

        if send_info is None:
            if bot_settings.mark_bad:
                await safe_set_message_reaction(
                    bot,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    reaction=ReactionTypeEmoji(emoji="👀"),
                    log_hint="admin no send_info",
                )
            return

        # Determine target chat and message ID
        if send_info.chat_for_id == message.chat.id:
            # We found it via resend_id (Forwarded Ticket). Target is source (User).
            target_chat = send_info.chat_from_id
            target_msg = send_info.message_id
        else:
            # We found it via message_id (Admin Reply). Target is destination (User).
            target_chat = send_info.chat_for_id
            target_msg = send_info.resend_id

        await safe_set_message_reaction(
            bot,
            chat_id=target_chat,
            message_id=target_msg,
            reaction=message.new_reaction[0],
            log_hint="admin proxy",
        )
        await safe_set_message_reaction(
            bot,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=ReactionTypeEmoji(emoji="👍"),
            log_hint="admin ack",
        )

    else:
        # Check if User is reacting to their own ticket (message_id=msg_id)
        send_info = await repo.get_message_resend_info(
            bot_id=bot.id, message_id=message.message_id, chat_from_id=message.chat.id
        )

        # If not, check if User is reacting to an Admin reply (resend_id=msg_id)
        if not send_info:
            send_info = await repo.get_message_resend_info(
                bot_id=bot.id, resend_id=message.message_id, chat_for_id=message.chat.id
            )

        if send_info:
            # Determine target chat and message ID
            if send_info.chat_from_id == message.chat.id:
                # Found via message_id (User Ticket). Target is destination (Master).
                target_chat = send_info.chat_for_id
                target_msg = send_info.resend_id
            else:
                # Found via resend_id (Received Reply). Target is source (Master).
                target_chat = send_info.chat_from_id
                target_msg = send_info.message_id

            await safe_set_message_reaction(
                bot,
                chat_id=target_chat,
                message_id=target_msg,
                reaction=message.new_reaction[0],
                log_hint="user proxy",
            )
            await safe_set_message_reaction(
                bot,
                chat_id=message.chat.id,
                message_id=message.message_id,
                reaction=ReactionTypeEmoji(emoji="👍"),
                log_hint="user ack",
            )


@router.my_chat_member()
async def on_my_chat_member(update: ChatMemberUpdated, bot: Bot):
    chat = update.chat
    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status
    bot_user = await bot.get_me()
    bot_info = f"Bot {bot_user.id} (@{bot_user.username})"

    if old_status != new_status:
        if new_status == ChatMemberStatus.MEMBER:
            logger.info(f"{bot_info} was added to chat {chat.id}")
            if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                await bot.send_message(
                    chat.id,
                    "Thanks for adding me to this chat! Please make me an admin to work properly.",
                )

        elif new_status == ChatMemberStatus.LEFT:
            logger.info(f"{bot_info} was removed from chat {chat.id}")

        elif new_status == ChatMemberStatus.ADMINISTRATOR:
            logger.info(f"{bot_info} permissions were updated in chat {chat.id}")
            if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                await bot.send_message(
                    chat.id,
                    "Thanks for making me an admin! To link this chat, run the /link command in the topic you want to link.",
                )

        elif new_status == ChatMemberStatus.RESTRICTED:
            logger.warning(f"{bot_info} permissions were restricted in chat {chat.id}")

        elif new_status == ChatMemberStatus.KICKED:
            logger.warning(f"{bot_info} was kicked from chat {chat.id}")

        else:
            logger.info(
                f"{bot_info} status changed in chat {chat.id} from {old_status} to {new_status}"
            )


@router.callback_query(LinkChatCallbackData.filter())
async def process_link_callback(
    callback: types.CallbackQuery,
    callback_data: LinkChatCallbackData,
    bot_settings: SupportBotSettings,
    config: BotConfig,
):
    if callback.from_user.id != bot_settings.owner:
        await callback.answer("Only the owner can use this command", show_alert=True)
        return

    callback_message = callback.message
    if callback_message is None or not isinstance(callback_message, types.Message):
        await callback.answer("Operation unavailable", show_alert=True)
        return

    if callback_data.action == "no":
        await callback_message.delete()
        await callback.answer("Operation cancelled")
    else:
        # Here you would implement the logic to save the link in your database
        # For example: save_chat_link(from_chat_id, to_chat_id, thread_id)
        bot_settings.master_chat = callback_data.new_chat_id
        bot_settings.master_thread = callback_data.new_thread_id
        bot_settings.can_work = False
        await config.update_bot_setting(bot_settings)
        await callback_message.edit_text(
            "Chat successfully linked!\n"
            "Now bot deactivated. "
            "You need to go to the bot admin panel and enable it.",
            reply_markup=None,
        )
        await callback.answer("Settings saved")


@router.message(F.migrate_to_chat_id)
async def on_migrate(
    message: Message, bot: Bot, bot_settings: SupportBotSettings, config: BotConfig
):
    old_chat_id = message.chat.id
    new_chat_id = message.migrate_to_chat_id
    assert new_chat_id is not None
    logger.info(f"Chat {old_chat_id} migrated to {new_chat_id}")

    bot_settings.can_work = False
    await config.update_bot_setting(bot_settings)

    await bot.send_message(
        chat_id=new_chat_id,
        text=f"Chat {old_chat_id} migrated to {new_chat_id}\n"
        f"Bot was stopped. You need relink bot to this chat. "
        f"Use /link command in the desired topic of this chat.",
    )
