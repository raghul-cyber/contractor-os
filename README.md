# ContractorOS

![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Status: Architecture Finalized](https://img.shields.io/badge/Status-Architecture%20Finalized-orange.svg)
![Build: Personal Project](https://img.shields.io/badge/Build-Personal%20Project-lightgrey.svg)

**A locally-run, fully agentic client-acquisition engine for solo technical contractors — it finds leads, researches them, writes personalized cold outreach, sends and follows up automatically, and tracks the whole deal pipeline in a built-in CRM. No SaaS subscriptions, no cloud lock-in.**

---

## Table of Contents
- [Overview](#overview)
- [Core Loop](#core-loop)
- [System Architecture](#system-architecture)
- [Module Breakdown](#module-breakdown)
- [Pipeline / Lead Lifecycle](#pipeline--lead-lifecycle)
- [LLM Router / Provider Fallback](#llm-router--provider-fallback)
- [Database Schema](#database-schema)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Safety & Manual Review](#safety--manual-review)
- [Cost Estimate](#cost-estimate)
- [Roadmap / Build Phases](#roadmap--build-phases)
- [Scale-Out Path](#scale-out-path)
- [License](#license)
- [Disclaimer](#disclaimer)

---

## Overview

Solo contractors (software, cybersecurity, AI, DevOps freelancers) spend enormous unpaid time on prospecting — finding companies, researching them, writing outreach, following up, tracking replies.

ContractorOS automates the entire top-of-funnel sales motion end-to-end as a single local application, so the contractor only steps in for the human parts: reading replies, booking calls, writing proposals, closing deals.

It runs locally on your machine, eliminating expensive monthly subscriptions and cloud lock-in.

---

## Core Loop

ContractorOS constantly runs in a loop, extracting leads, filtering them based on your custom criteria, composing context-aware emails, and tracking the responses.

```mermaid
flowchart TD
    A[Config: Skills + Target Sector] --> B[Hunter Agent]
    B --> C[Profiler Agent]
    C --> D[Craft Agent]
    D --> E[Outreach Agent]
    E --> F[CRM Agent]
    F --> G[Orchestrator Agent]
    G -.loops back.-> B
    F --> H{Reply or Interest Signal?}
    H -->|Yes| I[Meeting Booked]
    I --> J[Proposal Sent]
    J --> K[Deal Closed]
    H -->|No| G
```

---

## System Architecture

The application is built as a **modular monolith** — it uses one Python process containing six cooperating modules. This keeps deployment simple and fast, removing the overhead of managing microservices for a single-user system. All data and job scheduling is backed by SQLite, eliminating the need for external caching or queuing tools like Redis or Celery.

```mermaid
flowchart TB
    subgraph Config["Configuration"]
        CFG["config/*.yaml<br/>profile - targets - outreach - system"]
    end

    subgraph App["ContractorOS App - Single Process"]
        ORCH["Orchestrator<br/>LangGraph State Machine"]
        HUNT["Hunter Module"]
        PROF["Profiler Module"]
        CRAFT["Craft Module"]
        OUT["Outreach Module"]
        CRM["CRM Module"]
        DB[("SQLite DB<br/>contractor_os.db")]
        ROUTER["LLM Router<br/>Groq to Ollama to Gemini to NVIDIA"]
        SCHED["APScheduler<br/>SQLite Jobstore"]
        API["FastAPI Dashboard and API"]
    end

    subgraph External["External Services"]
        LLM["External LLM APIs"]
        APIFY["Apify Actors"]
        SMTP["SMTP and IMAP"]
        NOTIFY["Telegram and Discord"]
    end

    CFG --> ORCH
    ORCH --> HUNT --> PROF --> CRAFT --> OUT --> CRM
    CRM <--> DB
    ORCH <--> DB
    HUNT <--> DB
    PROF <--> DB
    CRAFT <--> DB
    OUT <--> DB

    ROUTER --> LLM
    HUNT --> APIFY
    OUT --> SMTP
    CRM --> NOTIFY

    PROF -.uses.-> ROUTER
    CRAFT -.uses.-> ROUTER
    ORCH -.uses.-> SCHED
    API <--> DB
```

---

## Module Breakdown

### 1. Hunter
**Role:** Acquires raw leads and normalizes data.
- **Responsibilities:** Executes scraping routines (Apify Google Maps, website extractors, Hunter.io) or manual CSV imports. Deduplicates on normalized domain before insert.
- **Inputs:** `targets.yaml`, manual CSVs.
- **Outputs:** Standardized raw leads in the database.

### 2. Profiler
**Role:** Deep-researches each raw lead to determine fit.
- **Responsibilities:** Scrapes website (Playwright), LinkedIn, and Google News. Uses LLM to synthesize a structured profile (industry, tech stack, pain points, personalization hooks). Computes a deterministic 0–1 `fit_score`. Low-fit leads are skipped to save costs.
- **Inputs:** Raw leads, company domain.
- **Outputs:** Structured JSON profile, `fit_score`.

### 3. Craft
**Role:** Writes highly personalized outreach sequences.
- **Responsibilities:** Generates a 4-part email sequence (initial + 3 follow-ups) in a single batched LLM call using hardcoded tone rules (no corporate boilerplate, one clear CTA, strict word counts).
- **Inputs:** Researched Lead Profile.
- **Outputs:** Drafted email sequences.

### 4. Outreach
**Role:** Handles all email sending, scheduling, and reply detection.
- **Responsibilities:** Sends emails (Resend/SMTP), enforces daily limits, schedules follow-ups (T+5/10/15), polls inbox via IMAP, classifies replies (LLM), and auto-cancels sequences upon reply/unsubscribe.
- **Inputs:** Drafted emails, IMAP inbox.
- **Outputs:** Delivered emails, Email Events (Sent/Replied/Bounced).

### 5. CRM
**Role:** Owns the pipeline state machine and generates daily reporting.
- **Responsibilities:** Transitions leads through states (RAW → RESEARCHED → DRAFTED etc.). Generates a daily plain-text digest (leads scraped, emails sent, hot leads, pipeline value) to Telegram/Discord.
- **Inputs:** Lead state changes, Outreach events.
- **Outputs:** Analytics, Telegram Digest.

### 6. Orchestrator
**Role:** The LangGraph state machine coordinating the pipeline.
- **Responsibilities:** Wires the modules together, runs the full cycle on a schedule (every 6 hours) or on-demand, wraps every stage in retry-with-backoff, and guarantees one failing lead never blocks the batch.
- **Inputs:** Triggers (Schedule/API).
- **Outputs:** Orchestrated workflow execution.

---

## Pipeline / Lead Lifecycle

Leads transition through strict sequential states, ensuring tasks execute in the correct order. The system handles sending follow-ups natively and detects replies. Leads can also be manually paused or transitioned at any time.

```mermaid
stateDiagram-v2
    [*] --> RAW
    RAW --> RESEARCHED
    RAW --> LOW_FIT
    LOW_FIT --> [*]

    RESEARCHED --> DRAFTED
    DRAFTED --> SENT

    SENT --> FU1_SENT
    FU1_SENT --> FU2_SENT
    FU2_SENT --> FU3_SENT
    FU3_SENT --> GHOSTED
    GHOSTED --> [*]

    SENT --> REPLIED
    FU1_SENT --> REPLIED
    FU2_SENT --> REPLIED
    FU3_SENT --> REPLIED

    REPLIED --> MEETING_BOOKED
    MEETING_BOOKED --> PROPOSAL_SENT
    PROPOSAL_SENT --> NEGOTIATING
    NEGOTIATING --> WON
    NEGOTIATING --> LOST
    WON --> [*]
    LOST --> [*]

    RESEARCHED --> PAUSED : manual pause
    DRAFTED --> PAUSED : manual pause
    SENT --> PAUSED : manual pause
    REPLIED --> PAUSED : manual pause
    PAUSED --> RESEARCHED : resume
```

---

## LLM Router / Provider Fallback

ContractorOS utilizes a resilient LLM routing system. If a provider times out or rate limits your request, the router automatically falls back to an alternative. Every API call is logged into the `llm_calls` table for cost and latency observability.

```mermaid
flowchart LR
    A[LLM Request] --> B[Groq]
    B -->|fail| C["Ollama (local)"]
    C -->|fail| D[Gemini]
    D -->|fail| E[NVIDIA NIM]
    B -->|success| F["Response<br/>logged to llm_calls"]
    C -->|success| F
    D -->|success| F
    E -->|success| F
    E -->|fail| G[AllProvidersFailedError]
```

---

## Database Schema

SQLite (`data/contractor_os.db`) manages the complete state of ContractorOS. Full DDL details can be found in `docs/ContractorOS_Architecture.md`.

<details>
<summary><strong>View ERD</strong></summary>

```mermaid
erDiagram
    LEADS ||--o{ OUTREACH_SEQUENCES : has
    LEADS ||--o{ EMAIL_EVENTS : has
    LEADS ||--|| PIPELINE : has
    LEADS ||--o{ ACTIVITY_LOG : logs
    OUTREACH_SEQUENCES ||--o{ EMAIL_EVENTS : triggers

    LEADS {
        int id PK
        string company_name
        string domain
        string email
        string status
        real fit_score
        string profile_json
    }
    OUTREACH_SEQUENCES {
        int id PK
        int lead_id FK
        string sequence_type
        string subject
        string status
        string scheduled_at
    }
    EMAIL_EVENTS {
        int id PK
        int lead_id FK
        int sequence_id FK
        string event_type
        string sentiment
    }
    PIPELINE {
        int id PK
        int lead_id FK
        string stage
        real contract_value
        string next_action_date
    }
    ACTIVITY_LOG {
        int id PK
        int lead_id FK
        string actor
        string action
    }
    LLM_CALLS {
        int id PK
        string provider
        string task_type
        int success
    }
    RUNS {
        int id PK
        int leads_scraped
        int emails_sent
    }
```
</details>

---

## Tech Stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| **Orchestration** | LangGraph | State machine wiring for the 6 modules |
| **Data** | SQLite + SQLAlchemy | Local, unified single source of truth |
| **Scheduling** | APScheduler | Task execution and email scheduling via SQLAlchemy jobstore |
| **API / UI** | FastAPI + HTMX | Single process REST API and interactive Dashboard |
| **Scraping** | Playwright + Apify | Headless data extraction and lead sourcing |
| **LLM Providers** | Groq, Ollama, Gemini, NVIDIA | Intelligence layer via fallback router |
| **Email** | Resend, SMTP, IMAP | Outreach delivery and reply detection |
| **Deployment** | Docker Compose | Spin up the App + optional local Ollama |

---

## Project Structure

```text
contractor-os/
├── app/
│   ├── api/                 # FastAPI routes and endpoints
│   ├── core/                # DB, config loader, logging, models
│   ├── modules/             # Business logic modules
│   │   ├── orchestrator/
│   │   ├── hunter/
│   │   ├── profiler/
│   │   ├── craft/
│   │   ├── outreach/
│   │   └── crm/
│   └── tests/               # Pytest suite
├── config/                  # User YAML configurations
├── data/                    # Local SQLite database
├── scripts/                 # Utility scripts (init_db, etc.)
└── Dockerfile               # Production container definition
```

---

## Getting Started

### Prerequisites
- Python 3.12+
- Docker (optional, for isolated environment or local Ollama)
- **API Keys**: Groq, Gemini, NVIDIA NIM (optional), Apify, Resend/SMTP

### Setup
1. **Clone the repository:**
   ```bash
   git clone https://github.com/raghul-cyber/contractor-os.git
   cd contractor-os
   ```

2. **Install dependencies (or run via Docker):**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

3. **Configure the environment:**
   ```bash
   cp .env.example .env
   ```
   Add your API keys to `.env`.

4. **Initialize the database:**
   ```bash
   python scripts/init_db.py
   ```

5. **Run the application:**
   ```bash
   docker compose up --build
   ```
   
> **Note:** It is highly recommended to start with the `dry_run` mode enabled in `system.yaml` to test prompts and logic before sending live emails!

---

## Configuration

ContractorOS uses hot-reloadable YAML configurations located in `config/`. Any changes take effect immediately without requiring a process restart.

- **`profile.yaml`**: Defines your skills, services, and unique value propositions.
- **`targets.yaml`**: Defines target sectors, company criteria, and pain signals.
- **`outreach.yaml`**: Manages daily send limits, email pacing, and send windows.
- **`system.yaml`**: Runtime knobs like batch size, cycle intervals, manual approval gates, and dry-run toggles.

---

## Safety & Manual Review

To protect your email reputation and deliverability, ContractorOS has strict safety rails built in:

- **`require_manual_approval`**: Gates every generated sequence behind a manual click in the dashboard. **We strongly recommend keeping this ON for the first month.**
- **`dry_run`**: Runs the entire orchestration pipeline and generates drafts, but skips the actual network dispatch for emails. Use this as a safe staging environment to tune your prompts and configuration.

---

## Cost Estimate

ContractorOS is highly efficient and designed to minimize ongoing operational costs.

| Component | Estimated Monthly Cost |
| --- | --- |
| LLM API (Groq/Gemini) | Free Tier (or pennies at scale) |
| Apify Lead Scraping | $5 – $20 |
| Email Sending (Resend) | Free Tier |
| Local LLM (Ollama) | $0 |
| **Total** | **~$10 – $35 / month** |

---

## Roadmap / Build Phases

- [x] Phase 1 — Foundation (DB schema, config loader, logging)
- [x] Phase 2 — LLM Router (Unified LLM adapter with fallback)
- [x] Phase 3 — Hunter
- [x] Phase 4 — Profiler
- [x] Phase 5 — Craft
- [x] Phase 6 — Outreach
- [x] Phase 7 — CRM + Orchestrator wiring
- [x] Phase 8 — Dashboard
- [ ] Phase 9 — Live cutover

---

## Scale-Out Path

ContractorOS is explicitly designed as a monolithic pipeline for personal use. However, because the logical boundaries are strictly separated by Python modules, scaling it out is straightforward. If volume exceeds 2,000 leads per day or requires multi-tenancy, the SQLite backend can be swapped for PostgreSQL, APScheduler swapped for Celery/Redis, and modules extracted into independent microservices. This is out of scope for v1.

---

## License

MIT License. This project is intended for individual/personal use.

---

## Disclaimer

This is a personal-use tool, not a commercial product. The user is solely responsible for ensuring that their automated email outreach complies with anti-spam regulations (e.g., CAN-SPAM, GDPR) within their operating jurisdictions.
