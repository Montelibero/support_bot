
import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.types import MessageReactionUpdated, ReactionTypeEmoji, Chat, User
from bot.routers.supports import router as support_router
from unittest.mock import MagicMock, AsyncMock

@pytest.mark.asyncio
async def test_reaction_flow(repo):
    # Setup
    bot = AsyncMock(spec=Bot)
    bot.id = 123456
    user_mock = MagicMock()
    user_mock.id = 123456
    user_mock.username = "test_bot"
    bot.get_me.return_value = user_mock
    
    dp = Dispatcher()
    dp.include_router(support_router)
    
    # Middleware
    from aiogram import BaseMiddleware
    class MockMiddleware(BaseMiddleware):
        def __init__(self, repo_instance, settings_instance):
            self.repo = repo_instance
            self.settings = settings_instance

        async def __call__(self, handler, event, data):
            data['repo'] = self.repo
            data['bot_settings'] = self.settings
            data['config'] = MagicMock()
            return await handler(event, data)

    settings = MagicMock()
    settings.master_chat = -100
    settings.mark_bad = True
    
    dp.update.middleware(MockMiddleware(repo, settings))
    
    # Pre-fill repo with a message connection
    # Let's say User sent message 100, Bot resent it as 200 in Master Chat
    USER_ID = 555
    MASTER_ID = -100
    await repo.save_message_ids(bot.id, USER_ID, message_id=100, resend_id=200, chat_from_id=USER_ID, chat_for_id=MASTER_ID)
    
    # Case 1: Master Chat reacts to message 200 -> Should propagate to User message 100
    reaction_update = types.Update(
        update_id=1,
        message_reaction=MessageReactionUpdated(
            chat=Chat(id=MASTER_ID, type='supergroup'),
            message_id=200,
            user=User(id=888, is_bot=False, first_name="Admin"),
            date=1234567890,
            old_reaction=[],
            new_reaction=[ReactionTypeEmoji(emoji='🔥')]
        )
    )
    
    await dp.feed_update(bot=bot, update=reaction_update)
    
    # Verify call to set_message_reaction on User Chat
    # We expect: 
    # 1. set_message_reaction(chat_id=USER_ID, message_id=100, reaction=[ReactionTypeEmoji(emoji='🔥')])
    # 2. set_message_reaction(chat_id=MASTER_ID, message_id=200, reaction=[ReactionTypeEmoji(emoji='👍')])
    
    # Check calls
    assert bot.set_message_reaction.call_count == 2
    
    # Inspect calls
    calls = bot.set_message_reaction.call_args_list
    
    # Call 1: Propagate to User
    call1_kwargs = calls[0].kwargs
    assert call1_kwargs['chat_id'] == USER_ID
    assert call1_kwargs['message_id'] == 100
    assert call1_kwargs['reaction'][0].emoji == '🔥'
    
    # Call 2: Ack to Admin
    call2_kwargs = calls[1].kwargs
    assert call2_kwargs['chat_id'] == MASTER_ID
    assert call2_kwargs['message_id'] == 200
    assert call2_kwargs['reaction'][0].emoji == '👍'

    # Reset mocks
    bot.set_message_reaction.reset_mock()
    
    # Case 2: User Chat reacts to message 100 -> Should propagate to Master message 200
    reaction_update_user = types.Update(
        update_id=2,
        message_reaction=MessageReactionUpdated(
            chat=Chat(id=USER_ID, type='private'),
            message_id=100,
            user=User(id=USER_ID, is_bot=False, first_name="User"),
            date=1234567890,
            old_reaction=[],
            new_reaction=[ReactionTypeEmoji(emoji='❤️')]
        )
    )
    
    await dp.feed_update(bot=bot, update=reaction_update_user)
    
    # Check calls
    assert bot.set_message_reaction.call_count == 2


