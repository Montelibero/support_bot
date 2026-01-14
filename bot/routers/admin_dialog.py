from aiogram import Bot
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, User, Message
from aiogram.utils.token import validate_token
from aiogram_dialog import DialogManager, Dialog, Window
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Select, Column, SwitchTo, ManagedCheckbox, Button
from aiogram_dialog.widgets.text import Const, Format

from config.bot_config import SupportBotSettings, delete_webhook, set_webhook, BotConfig


class AdminBotStates(StatesGroup):
    main = State()
    token = State()
    options = State()
    security_policy = State()
    start_message = State()
    master_chat = State()
    change_chat = State()
    transfer_bot = State()
    auto_message = State()


default_start_message = "Hello!\nAsk your question and we will answer you as soon as possible."
default_security_policy = """<b>Политика конфиденциальности</b>\n\n
Этот бот не хранит ваши сообщения, имя пользователя и @username. 
При отправке сообщения (кроме команд /start и /security_policy) ваш идентификатор пользователя 
записывается в кеш на некоторое время и потом удаляется из кеша. 
Этот идентификатор используется только для общения с оператором; \n\n
При отправке сообщения (кроме команд /start и /security_policy) оператор видит ваши имя пользователя, 
@username и идентификатор пользователя."""


async def choose_bot(callback: CallbackQuery, widget: Select, dialog_manager: DialogManager, item_id: str):
    state = dialog_manager.middleware_data['state']
    await state.update_data(bot_id=int(item_id))
    # print(f'Выбрана бота с id={item_id}')
    await dialog_manager.switch_to(state=AdminBotStates.options)


async def get_bots(event_from_user: User, dialog_manager: DialogManager, **kwargs):
    # pprint(kwargs)
    config: BotConfig = dialog_manager.middleware_data.get('config')
    bots = []
    user_bots = [item for item in config.get_bot_settings() if item.owner == event_from_user.id]
    for item in sorted(user_bots, key=lambda bot: (bot.username or "").lower()):
        if item.owner == event_from_user.id:
            text = item.username
            if item.can_work:
                text += ' [✔️]'
            bots.append((text, item.id))
    return {'bots': bots}


async def mh_get_token(message: Message, widget: MessageInput, dialog_manager: DialogManager) -> None:
    try:
        token = message.text
        if not token or not token.strip():
            await message.answer('Пожалуйста, введите токен бота.')
            return
            
        validate_token(token)

        async with Bot(token=token) as bot:
            bot_info = await bot.get_me()

        # Check if bot already exists
        config: BotConfig = dialog_manager.middleware_data.get('config')
        existing_bot = config.get_bot_setting(bot_info.id)
        if existing_bot:
            await message.answer(f'Бот @{bot_info.username} уже существует в системе.')
            return

        # Create new bot settings with default values
        new_bot = SupportBotSettings(
            id=bot_info.id,
            username=bot_info.username,
            token=token,
            start_message=default_start_message,
            security_policy=default_security_policy,
            master_chat=None,
            master_thread=None,
            no_start_message=False,
            special_commands=0,
            mark_bad=False,
            owner=message.from_user.id,
            can_work=False
        )

        # Save new bot settings
        await config.update_bot_setting(new_bot)

        await message.answer(f'Бот @{bot_info.username} успешно добавлен. Переходим к настройкам.')

        # Update dialog data and switch to options state
        state = dialog_manager.middleware_data['state']
        await state.update_data(bot_id=int(bot_info.id))
        await dialog_manager.switch_to(AdminBotStates.options)

    except (ValueError, TelegramBadRequest) as e:
        await message.answer(f'Некорректный ключ: {str(e)}')
    except Exception as e:
        await message.answer(f'Произошла ошибка при добавлении бота: {str(e)}')


async def mh_change_chat(message: Message, widget: MessageInput, dialog_manager: DialogManager) -> None:
    state = dialog_manager.middleware_data['state']
    data = await state.get_data()
    bot_id = data.get('bot_id')
    config: BotConfig = dialog_manager.middleware_data.get('config')
    bot_setting = config.get_bot_setting(bot_id)

    chat_data = message.text.split()
    if len(chat_data) >= 1:
        try:
            chat_id = int(chat_data[0])
            # Проверка формата ID чата
            if not str(chat_id).startswith('-100') and chat_id > 0:
                await message.answer("ID чата должен начинаться с -100 для групповых чатов. Пожалуйста, проверьте ID.")
                return
                
            topic_id = int(chat_data[1]) if len(chat_data) > 1 else None
            
            bot_setting.master_chat = chat_id
            bot_setting.master_thread = topic_id
            await config.update_bot_setting(bot_setting)

            if bot_setting.can_work:
                bot_setting.can_work = False
                await config.update_bot_setting(bot_setting)
                async with Bot(token=bot_setting.token) as temp_bot:
                    await delete_webhook(temp_bot)

            await message.answer(f"Чат успешно обновлен. ID чата: {chat_id}, \n"
                                 f"ID топика: {topic_id if topic_id else 'Не указан'}\n"
                                 f"Внимание! Бот деактивирован!")
            await dialog_manager.switch_to(AdminBotStates.options)
        except ValueError:
            await message.answer("Неверный формат. Пожалуйста, введите ID чата (и опционально ID топика) числами.")
    else:
        await message.answer("Пожалуйста, введите хотя бы ID чата.")


async def mh_change_start_message(message: Message, widget: MessageInput, dialog_manager: DialogManager) -> None:
    state = dialog_manager.middleware_data['state']
    data = await state.get_data()
    bot_id = data.get('bot_id')
    config: BotConfig = dialog_manager.middleware_data.get('config')
    bot_setting = config.get_bot_setting(bot_id)

    bot_setting.start_message = message.text
    await config.update_bot_setting(bot_setting)

    await message.answer("Приветственное сообщение успешно обновлено.")
    await dialog_manager.switch_to(AdminBotStates.options)


async def mh_change_security_policy(message: Message, widget: MessageInput, dialog_manager: DialogManager) -> None:
    state = dialog_manager.middleware_data['state']
    data = await state.get_data()
    bot_id = data.get('bot_id')
    config: BotConfig = dialog_manager.middleware_data.get('config')
    bot_setting = config.get_bot_setting(bot_id)

    bot_setting.security_policy = message.text
    await config.update_bot_setting(bot_setting)

    await message.answer("Политика конфиденциальности успешно обновлена.")
    await dialog_manager.switch_to(AdminBotStates.options)


async def mh_change_auto_reply(message: Message, widget: MessageInput, dialog_manager: DialogManager) -> None:
    state = dialog_manager.middleware_data['state']
    data = await state.get_data()
    bot_id = data.get('bot_id')
    config: BotConfig = dialog_manager.middleware_data.get('config')
    bot_setting = config.get_bot_setting(bot_id)

    bot_setting.auto_reply = message.text
    await config.update_bot_setting(bot_setting)

    await message.answer("Автоответ успешно обновлен.")
    await dialog_manager.switch_to(AdminBotStates.options)


async def mh_change_owner(message: Message, widget: MessageInput, manager: DialogManager) -> None:
    state = manager.middleware_data['state']
    data = await state.get_data()
    bot_id = data.get('bot_id')
    config: BotConfig = manager.middleware_data.get('config')
    bot_setting = config.get_bot_setting(bot_id)

    try:
        new_owner_id = int(message.text)
        # Проверяем, есть ли у нас чат с этим пользователем
        chat = await message.bot.get_chat(new_owner_id)

        # Если чат найден, меняем владельца
        old_owner = bot_setting.owner
        bot_setting.owner = new_owner_id
        await config.update_bot_setting(bot_setting)

        # Отправляем сообщения новому и старому владельцу
        await message.bot.send_message(new_owner_id, f"Вам передали бота @{bot_setting.username}")
        await message.bot.send_message(old_owner,
                                       f"Вы передали бота @{bot_setting.username} пользователю с ID {new_owner_id}")

        await message.answer("Бот успешно передан новому владельцу.")
        await manager.switch_to(AdminBotStates.main)
    except ValueError:
        await message.answer("Некорректный ID пользователя. Попробуйте еще раз.")
    except Exception as e:
        await message.answer(f"Ошибка при передаче бота: {str(e)}")


async def info_getter(dialog_manager: DialogManager, state: FSMContext, **kwargs):
    data = await state.get_data()
    bot_id = data.get('bot_id')
    config: BotConfig = dialog_manager.middleware_data.get('config')
    bot_setting = config.get_bot_setting(bot_id)

    can_work_text = '[✔️]' if bot_setting.can_work else '[❌]'
    mark_bad_text = '[✔️]' if bot_setting.mark_bad else '[❌]'
    local_names_text = '[✔️]' if bot_setting.use_local_names else '[❌]'
    ignore_commands_text = '[✔️]' if bot_setting.ignore_commands else '[❌]'
    use_auto_reply_text = '[✔️]' if bot_setting.use_auto_reply else '[❌]'
    block_links_text = '[✔️]' if bot_setting.block_links else '[❌]'

    chat_info = f"Чат: {bot_setting.master_chat}"
    if bot_setting.master_thread:
        chat_info += f", Топик: {bot_setting.master_thread}"

    # Сокращаем длинные тексты
    start_message_mini = bot_setting.start_message[:100] + '...' if len(
        bot_setting.start_message) > 100 else bot_setting.start_message
    security_policy_mini = bot_setting.security_policy[:100] + '...' if len(
        bot_setting.security_policy) > 100 else bot_setting.security_policy
    auto_reply_mini = bot_setting.auto_reply[:100] + '...' if len(
        bot_setting.auto_reply) > 100 else bot_setting.auto_reply

    return {
        "username": bot_setting.username,
        "chat_info": chat_info,
        "start_message": bot_setting.start_message,
        "security_policy": bot_setting.security_policy,
        "start_message_mini": start_message_mini,
        "security_policy_mini": security_policy_mini,
        "mark_bad": bot_setting.mark_bad,
        "can_work": bot_setting.can_work,
        "can_work_text": can_work_text,
        "mark_bad_text": mark_bad_text,
        "local_names_text": local_names_text,
        "ignore_commands_text": ignore_commands_text,
        "use_auto_reply_text": use_auto_reply_text,
        "auto_reply": bot_setting.auto_reply,
        "auto_reply_mini": auto_reply_mini,
        "block_links": bot_setting.block_links,
        "block_links_text": block_links_text
    }


async def checkbox_clicked(callback: CallbackQuery, checkbox: ManagedCheckbox,
                           dialog_manager: DialogManager):
    dialog_manager.dialog_data.update(is_checked=checkbox.is_checked())


async def button_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    state = manager.middleware_data['state']
    data = await state.get_data()
    bot_id = data.get('bot_id')
    config: BotConfig = manager.middleware_data.get('config')
    bot_setting = config.get_bot_setting(bot_id)

    if button.widget_id == 'mark_bad':
        bot_setting.mark_bad = not bot_setting.mark_bad
        await config.update_bot_setting(bot_setting)
    elif button.widget_id == 'use_auto_reply':
        bot_setting.use_auto_reply = not bot_setting.use_auto_reply
        await config.update_bot_setting(bot_setting)
    elif button.widget_id == 'local_names':
        bot_setting.use_local_names = not bot_setting.use_local_names
        await config.update_bot_setting(bot_setting)
    elif button.widget_id == 'ignore_commands':
        bot_setting.ignore_commands = not bot_setting.ignore_commands
        await config.update_bot_setting(bot_setting)
    elif button.widget_id == 'block_links':
        bot_setting.block_links = not bot_setting.block_links
        await config.update_bot_setting(bot_setting)
    elif button.widget_id == 'can_work':
        if not bot_setting.can_work:
            try:
                async with Bot(token=bot_setting.token) as temp_bot:
                    try:
                        await temp_bot.send_message(chat_id=bot_setting.master_chat,
                                                text="Проверка настроек чата",
                                                message_thread_id=bot_setting.master_thread)
                        # Устанавливаем вебхук
                        webhook_url = config.other_bots_url.format(bot_token=bot_setting.token)
                        await set_webhook(temp_bot, webhook_url)
                        bot_setting.can_work = True
                        await config.update_bot_setting(bot_setting)
                        await callback.answer("Бот успешно активирован!")
                    except TelegramBadRequest as e:
                        await callback.answer(f"Ошибка доступа к чату: {str(e)}", show_alert=True)
            except Exception as e:
                await callback.answer(f"Ошибка в настройках бота: {str(e)}", show_alert=True)
        else:
            try:
                async with Bot(token=bot_setting.token) as temp_bot:
                    await delete_webhook(temp_bot)
                bot_setting.can_work = False
                await config.update_bot_setting(bot_setting)
                await callback.answer("Бот деактивирован")
            except Exception as e:
                await callback.answer(f"Ошибка при деактивации бота: {str(e)}", show_alert=True)

    # elif button.widget_id in ['master_chat', 'master_thread']:
    #     # Если изменяется чат или топик, снимаем галочку "работает"
    #     bot_setting.can_work = False
    #     await bot_config.update_bot_setting(bot_setting)
    #     async with Bot(token=bot_setting.token) as temp_bot:
    #         await delete_webhook(temp_bot)
    #     await callback.answer("Настройки чата изменены. Бот деактивирован.")

    await manager.update({button.widget_id: getattr(bot_setting, button.widget_id)})


window_bot_config = Window(
    Format(text="<b>Настройки бота: </b> @{username}\n\n"
                "{chat_info}\n\n"
                "<b>Приветственное сообщение:</b>\n<blockquote>{start_message_mini}</blockquote>\n\n"
                "<b>Политика конфиденциальности:</b>\n<blockquote>{security_policy_mini}</blockquote>\n\n"
                "<b>Автоответ:</b>\n<blockquote>{auto_reply_mini}</blockquote>\n\n"
                "Ставить обезьянку - бот будет ставить обезьянку на все сообщения в чате "
                "которые не идут в ответ пользователю\n\n"
                "Игнорировать команды - бот будет игнорировать команды с установкой имени и прочие, "
                "требуется если несколько ботов поддержки слушают один чат\n\n"
                "Блокировать ссылки - бот будет блокировать сообщения со ссылками от пользователей, которым еще не ответил саппорт. "
                "При включении также все нетекстовые сообщения (фото, видео, стикеры и т.д.) считаются спамом и не принимаются."),
    SwitchTo(
        Const("Изменить приветствие"),
        state=AdminBotStates.start_message, id="to_hello",
    ),
    SwitchTo(
        Const("Изменить политику конфиденциальности"),
        state=AdminBotStates.security_policy, id="to_policy",
    ),
    SwitchTo(
        Const("Изменить чат"),
        state=AdminBotStates.change_chat, id="to_chat",
    ),
    Button(
        text=Format('{mark_bad_text} Ставить обезьянку'),
        id='mark_bad',
        on_click=button_clicked),
    Button(
        text=Format('{local_names_text} Использовать локальные имена'),
        id='local_names',
        on_click=button_clicked),
    Button(
        text=Format('{ignore_commands_text} Игнорировать команды'),
        id='ignore_commands',
        on_click=button_clicked),
    Button(
        text=Format('{can_work_text} Разрешить боту работать'),
        id='can_work',
        on_click=button_clicked),
    SwitchTo(
        Const("Изменить автоответ"),
        state=AdminBotStates.auto_message, id="to_auto",
    ),
    Button(
        text=Format('{use_auto_reply_text} Разрешить автоответ'),
        id='use_auto_reply',
        on_click=button_clicked),
    Button(
        text=Format('{block_links_text} Блокировать ссылки'),
        id='block_links',
        on_click=button_clicked),
    SwitchTo(
        Const("Передать бота"),
        state=AdminBotStates.transfer_bot, id="to_transfer",
    ),
    SwitchTo(
        Const("Вернуться к ботам"),
        state=AdminBotStates.main, id="to_main"
    ),
    state=AdminBotStates.options,
    getter=info_getter,
    parse_mode="html",
)

window_send_chat_id = Window(
    Const("Введите ID чата (обычно начинается с -100) и, если нужно, ID топика через пробел:"),
    MessageInput(mh_change_chat),
    SwitchTo(Const("Назад"), id="back_to_options", state=AdminBotStates.options),
    state=AdminBotStates.change_chat,
)

window_choose_bot = Window(
    Const(text='Выберите бота для редактирования настроек,'),
    Const(text='либо создайте новый'),
    Column(
        Select(
            Format('{item[0]}'),
            id='bots_select',
            item_id_getter=lambda x: x[1],
            items='bots',
            on_click=choose_bot
        )
    ),
    SwitchTo(Const("Добавить нового"), id="new", state=AdminBotStates.token),
    # SwitchTo(Const("Назад"), id="back_to_options", state=AdminBotStates.main),
    state=AdminBotStates.main,
    getter=get_bots
)

window_send_token = Window(
    Const(text='Пришлите ключ от бота'),
    MessageInput(
        func=mh_get_token,
        content_types=ContentType.ANY,
    ),
    SwitchTo(Const("Назад"), id="back_to_options", state=AdminBotStates.main),
    state=AdminBotStates.token,
)

window_change_start_message = Window(
    Const("Текущее приветственное сообщение:"),
    Format("<code>{start_message}</code>"),
    Const("\nВведите новое приветственное сообщение:"),
    MessageInput(mh_change_start_message),
    SwitchTo(Const("Назад"), id="back_to_options", state=AdminBotStates.options),
    state=AdminBotStates.start_message,
    getter=info_getter,
    parse_mode="html",
)

window_change_security_policy = Window(
    Const("Текущая политика конфиденциальности:"),
    Format("<code>{security_policy}</code>"),
    Const("\nВведите новую политику конфиденциальности:"),
    MessageInput(mh_change_security_policy),
    SwitchTo(Const("Назад"), id="back_to_options", state=AdminBotStates.options),
    state=AdminBotStates.security_policy,
    getter=info_getter,
    parse_mode="html",
)

window_transfer_bot = Window(
    Const("Введите ID пользователя, которому хотите передать бота:"),
    MessageInput(mh_change_owner),
    SwitchTo(Const("Отмена"), id="cancel_transfer", state=AdminBotStates.options),
    state=AdminBotStates.transfer_bot,
)

window_auto_reply = Window(
    Const("Текущий автоответ:"),
    Format("<code>{auto_reply}</code>"),
    Const("\nВведите новый автоответ:"),
    MessageInput(mh_change_auto_reply),
    SwitchTo(Const("Назад"), id="back_to_options", state=AdminBotStates.options),
    state=AdminBotStates.auto_message,
    getter=info_getter,
    parse_mode="html",
)

dialog_all = Dialog(
    window_bot_config,
    window_send_token,
    window_send_chat_id,
    window_choose_bot,
    window_change_start_message,
    window_change_security_policy,
    window_transfer_bot,
    window_auto_reply
)
