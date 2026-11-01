# Bookly

Bookly is a modular Telegram booking backend for small businesses. Clients browse services, select a specialist and a free time, manage their bookings, and receive confirmation and reminder messages. Admin commands provide basic service and booking management.

## Features

- Telegram client workflow for services, specialist, date, time, booking and cancellation.
- PostgreSQL persistence with SQLAlchemy 2 and Alembic.
- Per-specialist timezone, weekly working hours, breaks and date exceptions.
- PostgreSQL GiST exclusion constraint prevents overlapping confirmed appointments even under concurrent requests.
- Durable notification queue with retry/backoff and a one-hour reminder.
- Admin allowlist checked server-side for administrative commands.

## Stack

Python 3.12+, aiogram 3, SQLAlchemy 2 async, PostgreSQL 17, Alembic, Pydantic Settings, APScheduler, pytest, Ruff and Docker Compose.

## Architecture

`app/bot` contains Telegram handlers and inline keyboards. `app/services` contains booking, user and notification use cases. `app/database/models` defines relational entities; `app/database/session.py` owns async database setup. `app/core` contains configuration, logging and domain errors. `migrations` contains schema migration scripts.

The schema has users and roles; services; specialist profiles and service assignments; weekly schedules and date exceptions; bookings; and a notification outbox. Bookings use UTC instants, while slot generation converts to the specialist's IANA timezone. A PostgreSQL partial exclusion constraint blocks overlap only for confirmed bookings. Historical bookings remain attached to users and services, so referenced records are restricted from hard deletion.

## Requirements and configuration

Use Python 3.12+ and PostgreSQL 15+ (PostgreSQL 17 is used in Compose). Copy `.env.example` to `.env`, then set a newly issued Telegram bot token, database URL, admin Telegram IDs and business name. Never commit `.env` or put secrets in source control. `ADMIN_TELEGRAM_IDS` is a comma-separated allowlist. The database URL for local Python runs should point to localhost; Compose overrides it to use the `db` hostname.

## Run locally

```bash
cd ~/Projects/bookly
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
# Edit .env with your bot token and local PostgreSQL URL
alembic upgrade head
python -m app.main
```

## Run with Docker Compose

```bash
cd ~/Projects/bookly
cp .env.example .env
# Set BOT_TOKEN and ADMIN_TELEGRAM_IDS in .env
docker compose up --build
```

Compose starts PostgreSQL with a persistent volume and healthcheck. The app waits for DB health, runs migrations, then starts long polling. Stop with Ctrl+C; the database volume persists.

## Migrations

```bash
alembic upgrade head
alembic downgrade -1
```

The initial migration enables `btree_gist` and creates all tables and constraints. The database account must be allowed to install this PostgreSQL extension.

## Tests and lint

```bash
pytest
ruff check .
ruff format --check .
```

Domain validation tests run without a live database. Booking concurrency and migration behavior should additionally be exercised against PostgreSQL in CI; this repository does not bundle an ephemeral DB test harness.

## Using the bot

Clients: `/start`, `/book`, `/mybookings`, `/cancel <booking-id>`.

Allowlisted admins: `/admin_help`, `/addservice Name | description | minutes | price`, `/services`, `/invite_specialist Bio | timezone`, `/specialists`, `/assignservice Specialist_ID | Service_ID`, `/setschedule Specialist_ID | weekday | start | end | break_start-break_end`, `/today [YYYY-MM-DD]`, and `/clients`. Admins issue a 48-hour, single-use code with `/invite_specialist Bio | timezone`; the specialist claims it in a private chat with `/join_specialist CODE`. Only the code hash is stored. Weekdays are numbered Monday `0` through Sunday `6`; use `-` for no break. For example, `/setschedule SPECIALIST_UUID | 0 | 09:00 | 18:00 | 13:00-14:00` sets Monday hours in that specialist's timezone; replace `SPECIALIST_UUID` with the ID shown by `/specialists`. Assign the service before opening bookings.

## Current limitations

This first release supports one business instance per deployment. Admin commands support service creation, specialist registration, service assignment and recurring weekly hours. Rich edit/deactivate screens, specialist self-service schedule changes and date-exception management are not yet exposed in Telegram. Notification delivery is at-least-once: Telegram may accept a message just before a process failure, causing a retry duplicate. The reminder scheduler runs in-process, so deploy a single app replica unless notification claiming is moved to a distributed worker. REST API and web administration are intentionally out of scope.
