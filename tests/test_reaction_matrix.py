
import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.types import MessageReactionUpdated, ReactionTypeEmoji, Chat, User
from bot.routers.supports import router as support_router
from unittest.mock import MagicMock, AsyncMock

@pytest.mark.asyncio
async def test_reaction_matrix(repo):
    # Setup
    bot = AsyncMock(spec=Bot)
    bot.id = 123456
    user_mock = MagicMock()
    user_mock.id = 123456
    user_mock.username = "test_bot"
    bot.get_me.return_value = user_mock
    
    dp = Dispatcher()
    dp.include_router(support_router)
    
    class MockMiddleware(MagicMock):
        async def __call__(self, handler, event, data):
            data['repo'] = repo
            data['bot_settings'] = MagicMock()
            data['bot_settings'].master_chat = -100
            data['bot_settings'].mark_bad = True
            data['config'] = MagicMock()
            return await handler(event, data)

    dp.update.middleware(MockMiddleware())
    
    USER_ID = 555
    MASTER_ID = -100
    
    # --- Scenario 1: Ticket Flow ---
    # User sends Msg 100 -> Forwarded as 200 to Master
    await repo.save_message_ids(bot.id, USER_ID, message_id=100, resend_id=200, chat_from_id=USER_ID, chat_for_id=MASTER_ID)
    
    # 1.A: User reacts to 100 -> Should exist on 200
    update_1a = types.Update(update_id=1, message_reaction=MessageReactionUpdated(
        chat=Chat(id=USER_ID, type='private'), message_id=100,
        user=User(id=USER_ID, is_bot=False, first_name="User"),
        new_reaction=[ReactionTypeEmoji(emoji='A')], old_reaction=[], date=123
    ))
    await dp.feed_update(bot=bot, update=update_1a)
    assert bot.set_message_reaction.call_count == 2
    assert bot.set_message_reaction.call_args_list[0].kwargs['message_id'] == 200 
    bot.set_message_reaction.reset_mock()

    # 1.B: Admin reacts to 200 -> Should exist on 100
    update_1b = types.Update(update_id=2, message_reaction=MessageReactionUpdated(
        chat=Chat(id=MASTER_ID, type='supergroup'), message_id=200,
        user=User(id=888, is_bot=False, first_name="Admin"),
        new_reaction=[ReactionTypeEmoji(emoji='B')], old_reaction=[], date=123
    ))
    await dp.feed_update(bot=bot, update=update_1b)
    # Note: Admin reactions on Master chat trigger Ack reaction on User message, so call_count 2 is expected if Ack logic is active
    # The propagation is: set_message_reaction(chat_id=USER_ID, message_id=100, ...)
    assert bot.set_message_reaction.called
    # Check if propagation happened (target=100)
    calls = [c.kwargs['message_id'] for c in bot.set_message_reaction.call_args_list]
    assert 100 in calls
    bot.set_message_reaction.reset_mock()

    # --- Scenario 2: Reply Flow ---
    # Admin replies (Msg 300) in Master -> Sent to User as 400
    # DB: message_id=300, resend_id=400, chat_from_id=MASTER_ID, chat_for_id=USER_ID
    await repo.save_message_ids(bot.id, USER_ID, message_id=300, resend_id=400, chat_from_id=MASTER_ID, chat_for_id=USER_ID)
    
    # 2.A: User reacts to 400 (Received Reply) -> Should exist on 300 (Admin Original)
    update_2a = types.Update(update_id=3, message_reaction=MessageReactionUpdated(
        chat=Chat(id=USER_ID, type='private'), message_id=400,
        user=User(id=USER_ID, is_bot=False, first_name="User"),
        new_reaction=[ReactionTypeEmoji(emoji='C')], old_reaction=[], date=123
    ))
    await dp.feed_update(bot=bot, update=update_2a)
    assert bot.set_message_reaction.called, "User reacting to Admin reply failed"
    calls = [c.kwargs['message_id'] for c in bot.set_message_reaction.call_args_list]
    assert 300 in calls
    bot.set_message_reaction.reset_mock()
    
    # 2.B: Admin reacts to 300 (Their own reply) -> Should exist on 400
    update_2b = types.Update(update_id=4, message_reaction=MessageReactionUpdated(
        chat=Chat(id=MASTER_ID, type='supergroup'), message_id=300,
        user=User(id=888, is_bot=False, first_name="Admin"),
        new_reaction=[ReactionTypeEmoji(emoji='D')], old_reaction=[], date=123
    ))
    await dp.feed_update(bot=bot, update=update_2b)
    assert bot.set_message_reaction.called, "Admin reacting to their own reply failed"
    calls = [c.kwargs['message_id'] for c in bot.set_message_reaction.call_args_list]
    assert 400 in calls
