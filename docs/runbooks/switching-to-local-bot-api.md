# Switching to a self-hosted telegram-bot-api server

## When to use this runbook

Ты подключаешь этот сервис к уже существующему self-hosted `telegram-bot-api`
серверу (типовой сценарий: остальные боты fleet'а уже там, а этот один
торчит в cloud и мешает однородности). Цель — полностью убрать публичную
точку входа: домен, TLS-сертификат, reverse proxy и открытый порт наружу
становятся не нужны; весь трафик между `telegram-bot-api` и нашим aiohttp
идёт по внутренней сети.

Этот runbook применим и к обратному сценарию (вернуться на cloud), с тем
же набором шагов в другую сторону — см. раздел Rollback.

## Prerequisites

- Сервер `telegram-bot-api` уже поднят и принимает запросы на известном
  адресе (например `http://telegram-bot-api:8081/` внутри docker network,
  или `http://10.0.0.5:8081/` на внутреннем LAN). Слэш в конце обязателен
  — `TelegramAPIServer.from_base` ожидает базовый URL без суффикса
  `/bot{token}/{method}`.
- `telegram-bot-api` имеет исходящий доступ к `api.telegram.org` по
  MTProto — без этого он не сможет получать обновления от Telegram.
- `telegram-bot-api` может достать наш aiohttp сервис по выбранному
  внутреннему URL (см. Topology below).
- У тебя есть доступ к приватному чату с главным admin-ботом (токен
  `BOT_TOKEN`) под аккаунтом `ADMIN_ID`. Команда `/logout` работает
  только оттуда.
- Есть окно ≥ 15 минут — после `logOut` Telegram держит 10-минутный
  кулдаун на повторный логин на тот же API-сервер
  (`https://core.telegram.org/bots/api#logout`).

## Topology decisions

Что выставить в `BASE_URL` и `TELEGRAM_API_URL`, зависит от того, где
физически живёт `telegram-bot-api` относительно нашего контейнера.

### Вариант 1 — оба сервиса в одном docker-compose

Самый простой случай. `telegram-bot-api` и `supportbot` — сервисы одного
compose file, автоматически оказываются в общей default network, резолвят
друг друга по именам.

```env
TELEGRAM_API_URL=http://telegram-bot-api:8081/
BASE_URL=http://supportbot:80
```

Проверь, что имена сервисов (`telegram-bot-api`, `supportbot`) совпадают
с твоим compose. `ports:` наружу выставлять не нужно — всё общение внутри
docker network.

### Вариант 2 — сервисы в разных docker-compose, общая external network

`telegram-bot-api` уже крутится в своём проекте и принимает запросы от
fleet'а других ботов. Подключаешь наш supportbot к той же сети.

В compose supportbot'а:

```yaml
services:
  supportbot:
    # ...
    networks:
      - botapi_net

networks:
  botapi_net:
    external: true
    name: <имя_сети_где_telegram-bot-api>
```

`.env`:

```env
TELEGRAM_API_URL=http://telegram-bot-api:8081/
BASE_URL=http://supportbot:80
```

Имена `telegram-bot-api` и `supportbot` — это DNS-имена сервисов в этой
общей сети. Проверить можно командой `docker network inspect <сеть>` — в
`Containers` должны быть оба.

### Вариант 3 — `telegram-bot-api` на хосте, supportbot в docker

`telegram-bot-api` запущен как systemd service или просто бинарник на
том же хосте, не в docker. Тогда supportbot должен достучаться до хоста,
а telegram-bot-api — до supportbot.

Проще всего — биндить публикацию supportbot'а только на loopback:

```yaml
services:
  supportbot:
    ports:
      - "127.0.0.1:8080:80"  # снаружи недоступно, на хосте — да
    extra_hosts:
      - "host.docker.internal:host-gateway"  # Linux: явное мапирование
```

`.env`:

```env
TELEGRAM_API_URL=http://host.docker.internal:8081/
BASE_URL=http://127.0.0.1:8080
```

`telegram-bot-api` на хосте будет POST'ать на `127.0.0.1:8080` — это
наш контейнер. SupportBot будет ходить к Bot API через host gateway.
Публичного трафика — ноль.

### Вариант 4 — `telegram-bot-api` на другом хосте в приватной сети

WireGuard / internal VPC / private LAN. Подставляешь приватные IP /
hostname'ы:

```env
TELEGRAM_API_URL=http://10.0.0.5:8081/
BASE_URL=http://10.0.0.10:80
```

Главное — оба адреса должны быть достижимы с обеих сторон без
публичного интернета. Firewall на обоих хостах должен разрешать
этот трафик только внутри приватной сети.

---

Если ни один вариант не подходит — останови и опиши свою топологию,
дополним runbook.

## Migration steps

### 1. Проверь доступность нового Bot API

Из контейнера supportbot'а (или любого места, откуда он сможет ходить
после рестарта):

```bash
curl -fsS "${TELEGRAM_API_URL}bot<TOKEN>/getMe" | head
```

Ожидаем `{"ok":true, ...}`. Если нет — маршрут сломан, Bot API не
поднят или `TELEGRAM_API_URL` неверный. Остановись, чиним это до логаута
— иначе получишь 10-мин блокировку и час простоя.

### 2. Logout из текущего (cloud) API сервера

В личке главного admin-бота отправь:

```
/logout
```

Бот ответит подтверждением и начнёт проход по всем support-ботам. Каждые
10 результатов пришлёт отдельное сообщение `✅ botname (id)` / `❌ botname: error`.
Последним разлогинит сам себя — после этого admin-бот перестаёт отвечать,
это ожидаемо.

Открой `logs/SupportBot.log` и убедись, что для каждого бота есть строка
`WARNING Bot logged out — bot_id=... username=...`. Сохрани её / сделай
screenshot — пригодится, если что-то пойдёт не так на следующем шаге.

### 3. Правка `.env`

Выставь `TELEGRAM_API_URL` и `BASE_URL` согласно выбранной топологии
(см. раздел выше). Сохрани файл. Если используешь `docker-compose.yml`
с секцией `environment:` вместо `.env`, правь там — главное, чтобы
переменные попали в контейнер.

### 4. Опциональная очистка публичного маршрута

Если теперь хочешь полностью убрать публичную точку входа (рекомендуется,
иначе зачем был переход):

- В prod `docker-compose.yml`: убери `ports: - "80:80"` / `443:443` у
  supportbot сервиса, если он там есть.
- В nginx / traefik / caddy: удали `server { ... }` блок под поддомен
  этого бота.
- В DNS (Cloudflare / Route53 / куда настроен домен): удали
  A/AAAA/CNAME запись на бывший публичный host.
- В certbot / acme.sh: убери поддомен из списка на автообновление.
- В firewall / security group: если было правило «разрешить 443 на
  этот хост для этого поддомена» — удали.

Эти правки — ручные, зависят от твоего стека, делаются в твоих
репозиториях деплоя. Этот runbook не трогает `docker-compose.yml`
автоматически (stop-list в `AI_FIRST.md §10`).

### 5. Rebuild контейнера

```bash
just rebuild
```

Или вручную:

```bash
docker compose up -d --force-recreate
```

На старте `aiogram_on_startup_webhook` (`main.py:42`) сделает:
1. `set_commands` на главном admin-боте.
2. `delete_webhook` + `set_webhook` на главном admin-боте (уже через
   новый API сервер).
3. Проход по `bot_config.get_bot_settings()` с `can_work=True`: для
   каждого — `set_webhook` тоже через новый API.

## Verification

### 5.1. Startup логи чистые

```bash
docker compose logs --tail=100 supportbot 2>&1 | grep -E 'Started webhook|Unauthorized|set_webhook_error'
```

Ожидаем ровно одну строку `Started webhook`. Не должно быть
`Unauthorized` или `set_webhook_error`.

**Если видишь `Unauthorized`:**
- Прошло меньше 10 минут с `/logout`? Подожди, `just rebuild` снова.
- `TELEGRAM_API_URL` указывает на cloud по ошибке? Проверь `.env`.
- Токен бота битый? Посмотри в `bot_settings` таблице — `just
  docker compose exec supportbot sqlite3 data/support.db 'select id,
  username, token from bot_settings'`.

### 5.2. `telegram-bot-api` видит трафик

Из контейнера / хоста telegram-bot-api'я:

```bash
# Логи telegram-bot-api должны содержать свежие setWebhook, getMe
tail -50 /path/to/telegram-bot-api.log | grep -E 'setWebhook|getMe'
```

### 5.3. Обратный маршрут: telegram-bot-api → supportbot

Из контейнера `telegram-bot-api`:

```bash
curl -i "${BASE_URL}/${SECRET_URL}/main"
```

Ожидаем `HTTP/1.1 405 Method Not Allowed` (aiogram отдаёт это на GET —
подтверждение, что эндпоинт живой, но хочет POST). Любой другой ответ
(`Connection refused`, `timeout`, `404`) — маршрут сломан, правь топологию.

### 5.4. Живой admin-бот

Отправь главному admin-боту `/start`. Должен открыть админ-диалог
(`AdminBotStates.main`). Если молчит — webhook на admin-бота не встал,
смотри startup логи.

### 5.5. Живой support-бот end-to-end

Возьми один из support-ботов с `can_work=True`. С левого аккаунта, которого
нет в его `local_names` и который раньше не писал — отправь текстовое
сообщение. Ожидаем:
1. В master-чате появится forward'нутое сообщение с именем пользователя
   и его ID.
2. В `logs/SupportBot.log` — строка `Support bot message - Username: ...,
   Chat ID: ...` (`bot/routers/supports.py` cmd_message).
3. Ответ в thread'е уходит обратно автору.

Если тест-сообщение не долетело до master-чата — проверь `.5.3`
(маршрут с Bot API к нам) и `.5.2` (трафик в логах Bot API).

## Rollback (вернуться на cloud)

Процедура зеркальная:

1. `/logout` в личке admin-бота — сейчас logOut уйдёт на локальный
   сервер (фабрика `make_session` уважает env).
2. В `.env` — убери или закомментируй `TELEGRAM_API_URL`, верни
   `BASE_URL` на публичный https-домен.
3. Верни обратно в прод-compose публикацию портов, nginx vhost, DNS,
   сертификат — если удалял на шаге 4 миграции.
4. `just rebuild`.
5. 10-мин кулдаун на повторный логин в cloud.
6. Верификация по тем же пунктам.

## Known gotchas

- **10-минутный кулдаун** действует для каждого токена отдельно и на
  каждый API-сервер отдельно. Если дважды за 10 минут дёрнешь `/logout`
  с одного и того же сервера — второй раз получишь пачку `❌` в отчёте.
  Это не страшно, просто подожди.
- **`TELEGRAM_API_URL` без слэша в конце** — aiogram построит ломаный
  URL вида `http://host:8081bot123:ABC/getMe`. Проверь, что слэш на
  месте.
- **Смешанная конфигурация** (`TELEGRAM_API_URL` выставлен, но
  `BASE_URL` остался публичный) — технически работает: telegram-bot-api
  ходит в Telegram, а webhook POST'ает на публичный домен. Но ты
  платишь за домен и TLS без причины. Единственный легитимный кейс —
  переходный период, когда проверяешь, что Bot API сервер живой, а
  публичный маршрут ещё не выключил.
- **`telegram-bot-api` без интернета** — не будет получать обновления
  от Telegram по MTProto, всё встанет тихо. `getMe` будет отвечать, но
  пользовательские сообщения не долетят. Проверь исходящий доступ к
  `149.154.167.x` / `149.154.175.x` / etc. на этом хосте.
- **`telegram-bot-api` с флагом `--local`** — `getFile` отвечает
  локальными путями вместо URL. `is_local=True` в нашей фабрике это
  уже подставляет (`config/bot_config.py make_session`), aiogram
  корректно парсит. Но если у тебя на telegram-bot-api нет `--local`,
  не важно — `is_local=True` на стороне клиента безвреден.
- **Redis лок / webhook handshake** — если у прошлого запуска остались
  в Redis какие-то FSM состояния с `bot_id`, они переживут рестарт,
  это не связано с API сервером. Чистить только если ловишь странное
  поведение диалогов.
- **`can_work=False` у отдельных ботов** — startup loop пропускает их
  (`main.py:63`), webhook не ставится, пользователи у них не работают.
  Команда `/logout` **не трогает** `can_work`, так что этот статус
  сохраняется. Если хочешь включить — через админ-диалог кнопкой
  активации.

## Follow-up

- После успешного перехода удали из репозитория / секрет-стора старые
  TLS-сертификаты и ключи этого поддомена, если они там лежали.
- Добавь monitoring на `telegram-bot-api` сервер (health endpoint,
  дисковое пространство под кеш файлов, если используется `--local`).
- Если fleet ботов растёт, рассмотри возможность поднять запасной
  `telegram-bot-api` и balance между ними — это уже отдельная задача,
  вне scope этого runbook.
- Если пригодится откат — держи копию prod compose и nginx конфига
  до миграции в git (commit тегом `pre-local-bot-api-migration` или
  аналогичным).
