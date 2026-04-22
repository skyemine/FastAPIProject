# Prism

Prism is a self-hosted private messaging application built with FastAPI, WebSockets, and SQLAlchemy. It provides direct 1:1 messaging, friend requests, file sharing, avatar uploads, browser-based calling, and persistent account sessions for both local development and hosted deployment.

The project is designed to run locally with SQLite and scale to PostgreSQL for production environments.

## Features

- Direct 1:1 messaging with WebSocket delivery
- Account registration and login
- Server-side persistent sessions stored in the database
- Friend requests and accepted-friends-only messaging
- Avatar uploads with persistent storage
- File attachments with inline preview support
- Profile updates for username, display name, and password
- Basic 1:1 audio calling over WebRTC signaling
- Web push notification support for supported browsers
- SQLite for local use and PostgreSQL support for production
- Security headers, trusted hosts, HTTPS redirect support, and rate limiting

## Tech Stack

- Backend: FastAPI
- Realtime transport: WebSocket
- Database: SQLite or PostgreSQL
- ORM: SQLAlchemy
- Authentication: server-side database sessions with HTTP-only cookies
- Password hashing: `scrypt`
- Push notifications: Service Worker + Web Push (`pywebpush`)
- Frontend: vanilla HTML, CSS, and JavaScript

## Project Status

This is a working private messenger application, not a demo landing page. It includes a full browser UI, persistent account state, file delivery, avatar storage, and background notification support for compatible browsers.

Current limitations:

- It is not end-to-end encrypted
- Push notifications require HTTPS and a browser with Push API support
- Audio calls depend on browser WebRTC support and network conditions
- The in-memory rate limiter is suitable for a single instance, not distributed scaling

## Quick Start

### 1. Install dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. Start the server

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

### 3. Open the app

Open:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

## First Run

1. Register the first account.
2. Open a second browser profile or an incognito window.
3. Register a second account.
4. Send a friend request by username.
5. Accept the request from the second account.
6. Start messaging.

Important: different users on the same Wi-Fi network are supported correctly, but two accounts cannot share the same browser cookie jar. For simultaneous use of multiple accounts on one machine, use separate browser profiles or incognito windows.

## Configuration

The application reads configuration from environment variables.

### Core settings

- `APP_ENV`
  `development` or `production`
- `DATABASE_URL`
  Local example: `sqlite:///./messenger.db`
  PostgreSQL example: `postgresql+psycopg://user:password@host:5432/dbname`
- `SECRET_KEY`
  Required for secure session handling
- `SESSION_COOKIE_NAME`
  Cookie name for authenticated sessions
- `SESSION_MAX_AGE_SECONDS`
  Session lifetime in seconds

### Security settings

- `COOKIE_SECURE`
  Must be `true` behind HTTPS in production
- `FORCE_HTTPS`
  Redirect HTTP to HTTPS in production deployments
- `ALLOWED_HOSTS`
  Comma-separated hostnames or domains
- `ALLOWED_ORIGINS`
  Optional CORS origins if frontend and backend are split

### Push notification settings

- `PUSH_PUBLIC_KEY`
- `PUSH_PRIVATE_KEY`
- `PUSH_SUBJECT`

If push keys are not provided, Prism will generate local VAPID keys automatically on first startup and store them under `data/push/`.

## Local Storage

By default, local development uses:

- `messenger.db` for application data
- `uploads/` for attachments and avatars
- `data/push/` for generated VAPID keys when push is enabled automatically

If you want durable data on a hosted platform, make sure your database and file storage are persistent. SQLite on ephemeral hosting is not suitable for long-term production storage.

## Android and Browser Notifications

Push notifications are supported for compatible browsers when all of the following are true:

- the app is served over HTTPS
- the browser supports Service Workers and the Push API
- the user granted notification permission
- valid VAPID keys are available

For local HTTP development, browser notifications may still work while the page is open, but full background push behavior depends on browser security rules.

## Running with PostgreSQL

Set a PostgreSQL `DATABASE_URL` and start the app normally:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Recommended production setup:

- PostgreSQL for persistent data
- HTTPS reverse proxy
- `COOKIE_SECURE=true`
- `FORCE_HTTPS=true`
- a strong `SECRET_KEY`
- explicit `ALLOWED_HOSTS`

## Docker

Build:

```powershell
docker build -t prism .
```

Run:

```powershell
docker run --rm -p 8000:8000 `
  -e SECRET_KEY=replace-with-a-long-random-secret `
  -e DATABASE_URL=sqlite:///./messenger.db `
  prism
```

If you use Docker for production, mount persistent storage for the database and uploads or switch to PostgreSQL plus external file storage.

## Render / VPS Notes

For hosted deployment:

- use PostgreSQL instead of SQLite
- serve the app over HTTPS
- set `APP_ENV=production`
- define `ALLOWED_HOSTS` for your real domain
- keep `SECRET_KEY` private and long
- persist uploaded files if the platform does not provide a durable local filesystem

## Testing

Run the test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The automated tests cover:

- registration and login
- session persistence across app restarts
- separate sessions for multiple clients
- friend requests and direct chat
- file upload and download
- avatar persistence
- profile updates

## Main Files

- Backend entry: [main.py](C:/Users/Click/PycharmProjects/FastAPIProject/main.py)
- API and application setup: [app/api.py](C:/Users/Click/PycharmProjects/FastAPIProject/app/api.py)
- Database models: [app/models.py](C:/Users/Click/PycharmProjects/FastAPIProject/app/models.py)
- Security utilities: [app/security.py](C:/Users/Click/PycharmProjects/FastAPIProject/app/security.py)
- Frontend markup: [app/static/index.html](C:/Users/Click/PycharmProjects/FastAPIProject/app/static/index.html)
- Frontend logic: [app/static/app.js](C:/Users/Click/PycharmProjects/FastAPIProject/app/static/app.js)
- Frontend styles: [app/static/styles.css](C:/Users/Click/PycharmProjects/FastAPIProject/app/static/styles.css)
- Service worker: [app/static/service-worker.js](C:/Users/Click/PycharmProjects/FastAPIProject/app/static/service-worker.js)

## Security Notes

Prism includes a solid baseline for a private self-hosted messenger:

- `scrypt` password hashing
- database-backed session persistence
- HTTP-only cookies
- trusted host validation
- security headers
- HTTPS redirect support
- auth and message rate limiting

However, it should not be described as end-to-end secure messaging. The server can still access message content, and production hardening depends on your deployment choices.
