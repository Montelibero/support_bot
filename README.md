

# Support Bot System

Система ботов поддержки, которая может работать как в режиме одиночного бота, так и в режиме мультибота.

## Установка

### Рекомендуемый способ (Docker pull)

1. Скачайте готовый образ:
```bash
docker pull ghcr.io/montelibero/support_bot:latest
```

2. Создайте файл `.env` в корневой директории проекта

### Альтернатива: установка из исходников

1. Клонируйте репозиторий
```bash
git clone <repository_url>
cd support-bot-system
```

2. Установите зависимости через uv
```bash
uv sync
```

3. Создайте файл `.env` в корневой директории проекта

## Режимы работы

### 1. Одиночный бот (Single Bot Mode)

В этом режиме запускается один бот поддержки. Идеально подходит для небольших проектов или тестирования.

#### Настройка .env для одиночного бота:
```env
# Обязательные параметры
SINGLE_BOT_TOKEN=your_bot_token
REDIS_URL=redis://localhost:6379/0
ADMIN_ID=your_admin_id

# Опциональные параметры
SINGLE_START_MESSAGE="Здравствуйте! Чем могу помочь?"
SINGLE_SECURITY_POLICY=default
SINGLE_MASTER_CHAT=0  # ID чата поддержки (0 = использовать ADMIN_ID)
SINGLE_USE_AUTO_REPLY=false
SINGLE_AUTO_REPLY="Message automatically forwarded to support. Please wait for a response."
SENTRY_DSN=your_sentry_dsn  # опционально
```

#### Запуск одиночного бота:
```bash
uv run single_bot.py
```

### 2. Мультибот (Multi Bot Mode)

В этом режиме может работать множество ботов поддержки одновременно. Подходит для крупных проектов или агентств.

#### Настройка .env для мультибота:
```env
# Обязательные параметры
BOT_TOKEN=your_main_bot_token
REDIS_URL=redis://localhost:6379/0
ADMIN_ID=your_admin_id
BASE_URL=https://your-domain.com

# Опциональные параметры
ENVIRONMENT=production  # для webhook режима
WEB_SERVER_HOST=127.0.0.1
WEB_SERVER_PORT=8000
SENTRY_DSN=your_sentry_dsn
```

#### Запуск мультибота:

1. В режиме поллинга (для разработки):
```bash
uv run main.py
```

2. В режиме вебхука (для продакшена):
```bash
ENVIRONMENT=production uv run main.py
```

## Конфигурация ботов

### Одиночный бот
- Все настройки берутся из переменных окружения с префиксом `SINGLE_`
- Конфигурация создается автоматически при запуске

### Мультибот
- Боты добавляются через административный интерфейс главного бота
- Конфигурация хранится в базе данных `data/support.db`
- Каждый бот может иметь свои настройки:
  - Приветственное сообщение
  - Политика безопасности
  - ID чата поддержки
  - Автоответы
  - и другие параметры

## Переключение между Telegram cloud и локальным Bot API сервером

Систему можно направить на собственный [Bot API server](https://core.telegram.org/bots/api#using-a-local-bot-api-server)
без правки кода.

### Переменная окружения

- `TELEGRAM_API_URL` — базовый URL локального Bot API сервера (например,
  `http://telegram-bot-api:8081/`). Если переменная не задана, используется
  стандартный `https://api.telegram.org`.

Все инстансы `aiogram.Bot` создаются через фабрику `config.bot_config.make_bot`,
которая читает эту переменную на старте.

### Порядок переключения

Перед сменой API сервера бот нужно разлогинить из текущего — Telegram не
отдаёт обновления, пока токен привязан к другому серверу. После `logOut`
действует 10-минутный кулдаун на повторный логин на тот же сервер
([docs](https://core.telegram.org/bots/api#logout)).

1. В личке с главным admin-ботом отправьте `/logout` (команда доступна только
   владельцу — `ADMIN_ID`). Бот разом разлогинит все support-боты и сам себя,
   отправив отчёт батчами по 10 штук.
2. Правьте `.env`: выставите или уберите `TELEGRAM_API_URL`.
3. Пересоберите контейнер: `just rebuild` (или
   `docker compose up -d --force-recreate`).
4. На старте `aiogram_on_startup_webhook` поставит свежие webhook-и уже на
   новом сервере.

Если рестарт случился раньше, чем прошёл 10-минутный кулдаун на предыдущем
сервере, startup упадёт с `Unauthorized` для каждого токена — подождите и
повторите `just rebuild`.

> Полная пошаговая инструкция с учётом топологии, откатом и диагностикой
> сломанных маршрутов — [docs/runbooks/switching-to-local-bot-api.md](docs/runbooks/switching-to-local-bot-api.md).

## Логирование

Логи сохраняются в директории `logs/`:
- `logs/SupportBot.log` - для мультибота
- `logs/SingleSupportBot.log` - для одиночного бота

## Мониторинг

- Интеграция с Sentry для отслеживания ошибок (опционально)
- Уведомления администратору при запуске/остановке ботов

## Требования

- Python 3.12+
- uv
- Redis
- Доступ к API Telegram
- Для webhook режима: SSL сертификат и домен

## Команды качества

Базовый набор команд для локальной проверки (AI-first bootstrap):

```bash
just test       # Полный запуск тестов (можно передать аргументы: just test "-q")
just test-fast  # Быстрая выборка smoke-тестов
just lint       # Ruff lint (стартовая область: customizations + startup path)
just fmt        # Ruff format для стартовой области
just types      # Pyright type-check для стартовой области
just check-changed  # Проверка только измененных .py файлов (ruff+pyright)
just arch-test  # Базовая структурная проверка обязательной документации
just check      # format --check + lint + types + test-fast
```

## Запуск через Docker (для режима одиночного бота)

1. Убедитесь, что у вас установлен Docker и Docker Compose.
2. Настройте `.env` файл (см. раздел "Настройка .env для одиночного бота").
3. Запустите бота:
```bash
docker compose up -d
```
Бот запустится вместе с необходимым ему Redis. По умолчанию `docker-compose.yml` использует образ `ghcr.io/montelibero/support_bot:latest`.

### Сборка образа вручную (опционально)

Если нужно собрать образ самостоятельно и использовать его в `docker-compose.yml`:
```bash
docker build -t support_bot:local .
```
