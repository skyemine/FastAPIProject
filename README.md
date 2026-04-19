# PulseChat

`PulseChat` - це self-hosted месенджер на `FastAPI` з приватними чатами 1:1, авторизацією, WebSocket-з'єднанням і готовим шляхом як для локального запуску, так і для деплою на хостинг.

## Що вже вміє

- браузерний UI для direct messages і заявок у друзі
- реєстрація та логін через HTTP-only cookie-сесію
- WebSocket чат між друзями
- історія повідомлень у БД
- `SQLite` для локального старту
- `PostgreSQL` для продакшн-деплою
- rate limiting для auth і відправки повідомлень
- password policy для нових акаунтів
- security headers, HSTS, trusted hosts, optional HTTPS redirect
- Docker і `compose.yaml` для швидкого підняття

## Швидкий локальний запуск

### Варіант 1: найпростіше, через PowerShell

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start_local.ps1
```

Після цього відкрий:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

### Варіант 2: вручну

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## Перший запуск

1. Відкрий браузер на `http://127.0.0.1:8000`
2. Створи акаунт через `Register`
3. Створи другий акаунт в іншому браузері або інкогніто
4. Надішли заявку в друзі по `username`
5. Прийми заявку другим акаунтом і відкрий direct chat

Так ти одразу побачиш realtime-чат у роботі.

## Конфігурація

Основні змінні в [`.env.example`](C:/Users/Click/PycharmProjects/FastAPIProject/.env.example):

- `DATABASE_URL` - `sqlite:///./messenger.db` локально або `postgresql+psycopg://...` у проді
- `SECRET_KEY` - обов'язково заміни на довгий випадковий ключ перед публічним деплоєм
- `APP_ENV=production` - вмикай для публічного деплою
- `COOKIE_SECURE=true` - обов'язково, якщо сайт працює по HTTPS
- `FORCE_HTTPS=true` - обов'язково за reverse proxy з TLS
- `ALLOWED_HOSTS` - додай свій домен або IP
- `ALLOWED_ORIGINS` - якщо фронт буде на іншому домені

## Docker

### Один контейнер

```powershell
docker build -t pulsechat .
docker run --rm -p 8000:8000 `
  -e SECRET_KEY=replace-me `
  -e DATABASE_URL=sqlite:///./messenger.db `
  pulsechat
```

### З PostgreSQL через compose

```powershell
docker compose up --build
```

Після запуску застосунок буде на:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

## Деплой на хостинг

Для VPS або контейнерного хостингу логіка проста:

1. Скопіюй код на сервер
2. Задай змінні середовища
3. Використай `PostgreSQL`
4. Постав reverse proxy з HTTPS
5. Увімкни:

```text
COOKIE_SECURE=true
FORCE_HTTPS=true
ALLOWED_HOSTS=your-domain.com
ALLOWED_ORIGINS=https://your-domain.com
```

Команда запуску:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Що вже підсилено

У цій версії вже є:

- паролі хешуються через `scrypt`
- сесії зберігаються в `HTTP-only` cookie
- базовий rate limiting
- мінімальна політика складності пароля
- trusted hosts
- security headers і HSTS для HTTPS
- готовність працювати за HTTPS reverse proxy
- перевірка, що в production не лишився дефолтний `SECRET_KEY`

Але важливо чесно:

- це ще не end-to-end encryption
- це ще не розподілений кластер
- in-memory rate limiter підходить для одного інстансу, а не для горизонтального масштабування

Для наступного рівня варто додати:

1. Redis для shared rate limiting і presence
2. Alembic для міграцій
3. E2EE поверх клієнта
4. attachment storage
5. moderation / roles / invite links

## Корисні файли

- сервер: [app/api.py](C:/Users/Click/PycharmProjects/FastAPIProject/app/api.py)
- auth і security: [app/security.py](C:/Users/Click/PycharmProjects/FastAPIProject/app/security.py)
- UI: [app/static/index.html](C:/Users/Click/PycharmProjects/FastAPIProject/app/static/index.html)
- стилі: [app/static/styles.css](C:/Users/Click/PycharmProjects/FastAPIProject/app/static/styles.css)
- frontend logic: [app/static/app.js](C:/Users/Click/PycharmProjects/FastAPIProject/app/static/app.js)
- локальний старт: [start_local.ps1](C:/Users/Click/PycharmProjects/FastAPIProject/start_local.ps1)
- Docker: [Dockerfile](C:/Users/Click/PycharmProjects/FastAPIProject/Dockerfile)
- compose: [compose.yaml](C:/Users/Click/PycharmProjects/FastAPIProject/compose.yaml)

## Тести

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
