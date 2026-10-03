# Tech Jobs Telegram Bot

A background worker that checks tech job listings every 10 minutes and posts them to a Telegram forum supergroup organized by topics.

Covers Egypt, Saudi Arabia, the UAE, and Remote roles.

---

## Features

- **Sources**: WUZZUF (primary Egypt/Gulf tech aggregator), LinkedIn public guest search, and Remotive (remote tech jobs API).
- **Deduplication**: 3-tier SQLite deduplication to prevent reposting existing jobs.
- **Topic Routing**: Sends jobs to specific forum topics based on role and location.
- **Spam Filtering**: Automatically drops listings from unwanted agencies using `MUTED_COMPANIES`.
- **Per-Topic Delivery**: Tracks send status per topic so failed deliveries can be retried safely.
- **Seed Mode**: Allows initial database population without sending messages to Telegram.
- **Railway Ready**: Pre-configured for deployment on Railway with automatic restarts and health checks.

---

## Topics and Environment Variables

You can configure any of the following topics in your `.env` or Railway settings. If a topic variable is empty or not set, that topic is skipped.

### Role and Location Topics

| Topic | Variable | Description |
|---|---|---|
| Software Engineering | `TOPIC_SWE` | Full Stack and general software engineering |
| Backend | `TOPIC_BACKEND` | Python, Node.js, Java, .NET, Go, PHP, APIs |
| Frontend / Web | `TOPIC_FRONTEND` | React, Vue, Angular, Next.js, HTML/CSS |
| Mobile | `TOPIC_MOBILE` | iOS, Android, Flutter, React Native, Swift |
| QA & Testing | `TOPIC_QA` | QA, SDET, automation, software testing |
| DevOps & Cloud | `TOPIC_DEVOPS` | DevOps, SRE, Cloud, Kubernetes, Terraform |
| Cybersecurity | `TOPIC_CYBERSECURITY` | InfoSec, pentesting, SOC, security engineering |
| Data & AI | `TOPIC_DATA_AI` | Data engineering, data science, ML, AI, BI |
| IT Support | `TOPIC_IT_SUPPORT` | SysAdmin, helpdesk, network, ERP support |
| Product | `TOPIC_PRODUCT` | Product managers, project managers, analysts |
| Design | `TOPIC_DESIGN` | UI/UX, product design, graphic design |
| Egypt | `TOPIC_EGYPT` | All jobs located in Egypt |
| Remote | `TOPIC_REMOTE` | All remote and work-from-home jobs |

---

## Setup Instructions

### 1. Telegram Setup

1. Create a Telegram Supergroup and enable **Topics** in Group Settings.
2. Create a bot using [@BotFather](https://t.me/BotFather) and save the bot token.
3. Add the bot to your supergroup as an **Administrator** with permission to manage topics and post messages.
4. Run the helper script to get your `TELEGRAM_GROUP_ID` and topic thread IDs:
   ```bash
   python get_topic_ids.py
   ```
5. Send a message into each topic in Telegram. The script will print the thread ID for each topic.

### 2. Local Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Your `.env` file should look like this:

```env
TELEGRAM_BOT_TOKEN="your_bot_token_here"
TELEGRAM_GROUP_ID="-100xxxxxxxxxx"

TOPIC_SWE="3"
TOPIC_QA="5"
TOPIC_DEVOPS="7"
TOPIC_CYBERSECURITY="9"
TOPIC_DATA_AI="11"
TOPIC_IT_SUPPORT="13"
TOPIC_PRODUCT="15"
TOPIC_DESIGN="17"
TOPIC_EGYPT="19"
TOPIC_REMOTE="21"

# Optional divided topics
TOPIC_BACKEND=""
TOPIC_FRONTEND=""
TOPIC_MOBILE=""

SEED_MODE="false"
MUTED_COMPANIES="micro1,Hire Feed,Jobs AI,Hired,micro1 AI,SomeOtherSpammer,Quik Hire Staffing"
```

---

## Railway Deployment

1. Push this repository to GitHub.
2. In Railway, click **New Project** -> **Deploy from GitHub repo**.
3. Go to **Variables** and add your configuration (see table above).
4. *(Recommended)* Add a persistent volume mounted at `/app` or `/app/jobs.db` to keep the SQLite database across restarts.
5. On first launch, set `SEED_MODE=true` for 10 minutes so existing jobs are indexed without sending hundreds of messages. Then switch `SEED_MODE=false`.

---

## Configuration Reference

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Required | Bot token from BotFather |
| `TELEGRAM_GROUP_ID` | Required | Supergroup chat ID (starts with `-100`) |
| `SEED_MODE` | `false` | Save jobs to database without posting |
| `FETCH_INTERVAL_SECONDS` | `600` | Polling interval in seconds (default: 10 minutes) |
| `TELEGRAM_SEND_DELAY` | `1.5` | Delay in seconds between message sends |
| `MUTED_COMPANIES` | `""` | Comma-separated list of companies/spammers to drop |
| `DB_PATH` | `jobs.db` | Path to SQLite database file |
| `WUZZUF_MAX_PAGES_PER_SEARCH` | `1` | Max pages to scrape per WUZZUF query |
| `LINKEDIN_MAX_PAGES_PER_SEARCH` | `1` | Max pages to scrape per LinkedIn query |
| `LINKEDIN_FRESHNESS_SECONDS` | `3600` | LinkedIn lookback window |

---

## Local Development & Testing

Run all unit and integration tests:

```bash
python -m pytest
```

Run a single scrape and delivery cycle:

```bash
python worker.py --once
```

Run the continuous worker:

```bash
python worker.py
```