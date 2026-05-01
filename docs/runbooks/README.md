# Runbooks

Runbooks describe repeatable operational procedures for common incidents.

## Recommended Structure

Each runbook should include:

1. **Symptoms**: what is observed
2. **Checks**: commands and signals to inspect
3. **Likely causes**: top hypotheses
4. **Recovery steps**: ordered actions
5. **Verification**: how to confirm recovery
6. **Follow-up**: preventive improvements

## Initial Index

- [switching-to-local-bot-api.md](switching-to-local-bot-api.md) —
  миграция сервиса с cloud Bot API на self-hosted `telegram-bot-api`,
  уход от публичного домена и TLS.
- [reactions-in-restricted-chats.md](reactions-in-restricted-chats.md) —
  что делать, если бот не ставит реакции в чате и в логи сыпется
  `Bad Request: REACTION_INVALID`.
- (to add) Bot startup failures
- (to add) Webhook delivery issues
- (to add) Redis connectivity issues
- (to add) Helper ACK timeout handling
