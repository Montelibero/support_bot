

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
