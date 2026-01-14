from aiogram import Router, types
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, Message
from .interface import AbstractBotCustomization
from .registry import register_customization
from config.bot_config import SupportBotSettings
from database.redis_tools import save_to_redis

class GetCallbackData(CallbackData, prefix="get"):
    user_id: int
    username: str

class EndCallbackData(CallbackData, prefix="end"):
    user_id: int
    username: str

@register_customization(bot_id=5173438724)
class HelperCustomization(AbstractBotCustomization):
    def __init__(self):
        self._router = Router()
        self._register_handlers()

    @property
    def router(self) -> Router:
        return self._router

    def _register_handlers(self):
        self._router.callback_query.register(self.callbacks_lang_get, GetCallbackData.filter())
        self._router.callback_query.register(self.callbacks_lang_end, EndCallbackData.filter())

    async def get_extra_text(self, user: types.User, message: Message, bot_settings: SupportBotSettings) -> str:
        return f'\n/get_info_{user.id}@mymtlbot'

    async def get_reply_markup(self, user: types.User, message: Message, bot_settings: SupportBotSettings) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text='Взять',
                                       callback_data=GetCallbackData(user_id=user.id,
                                                                     username=str(user.username)).pack())]])

    async def callbacks_lang_get(self, callback: types.CallbackQuery, callback_data: GetCallbackData):
        await save_to_redis(callback.message.chat.id, {'user_id': callback_data.user_id,
                                                       'username': callback_data.username,
                                                       'agent_username': callback.from_user.username,
                                                       'url': callback.message.get_url()})

        await callback.answer(f'Задача закрепляется за {callback.from_user.username}')
        reply_markup = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text=f'Взял {callback.from_user.username}',
                                       callback_data=EndCallbackData(user_id=callback.from_user.id,
                                                                     username=callback.from_user.username).pack())]])
        await callback.message.edit_reply_markup(reply_markup=reply_markup)

    async def callbacks_lang_end(self, callback: types.CallbackQuery, callback_data: EndCallbackData):
        if callback_data.user_id == 0:
            await callback.answer(f'Задача закрыта {callback_data.username} !')
            return
        if callback_data.user_id != callback.from_user.id:
            await callback.answer(f'Задача закреплена за {callback_data.username} !', show_alert=True)
            return

        await save_to_redis(callback.message.chat.id, {'user_id': callback_data.user_id,
                                                       'url': callback.message.get_url(),
                                                       'closed': True})
        reply_markup = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text=f'Закрыл {callback.from_user.username}',
                                       callback_data=EndCallbackData(user_id=0,
                                                                     username=callback_data.username).pack())]])
        await callback.message.edit_reply_markup(reply_markup=reply_markup)
        await callback.answer(f'{callback_data.username} умничка !')
