# AI Email Copilot

A production-minded, multi-user web application that connects to Gmail, analyzes incoming messages with Hugging Face models, identifies tasks and deadlines, and helps users manage their inbox.

> **Safeguard Guarantee**: Every outgoing email requires explicit user review and confirmation before sending. Zero autonomous sends.

---

## Architecture

- **Frontend**: React 19 + TypeScript + Vite + Lucide
- **Backend API**: FastAPI (Python 3.11, async architecture)
- **Database**: PostgreSQL 17 (SQLAlchemy 2.0 async + Alembic migrations)
- **Security**: Fernet symmetric encryption for OAuth tokens, HMAC-SHA256 CSRF protection, input sanitization against prompt injection
- **AI / NLP**: Hugging Face Transformers (classification, executive summarization, action item & deadline extraction, contextual draft generation)
- **Integrations**: Google OAuth 2.0 (restricted scopes: `gmail.readonly`, `gmail.send`)

---

## Project Structure

```
ai-email-copilot/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app & health checks & static frontend mount
│   │   ├── config.py            # Pydantic Settings
│   │   ├── api/                 # API routers (auth, emails, tasks, replies)
│   │   ├── services/            # Gmail, OAuth, Crypto, and AI services
│   │   ├── workers/             # Async background jobs
│   │   ├── db/                  # Models (SQLAlchemy 2.0) & Session
│   │   └── schemas/             # Pydantic schemas (Email, Task, Reply, User)
│   ├── tests/                   # 15 Automated Pytest tests
│   ├── alembic/                 # Migration scripts
│   ├── requirements.txt         # Python dependencies
│   ├── Dockerfile
│   └── .env
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # Dashboard, Inbox, Task Board & Reply Approval Modal
│   │   ├── types.ts             # TypeScript data models
│   │   ├── services/api.ts      # Backend API client
│   │   └── index.css            # Modern responsive stylesheet
│   ├── package.json
│   └── vite.config.ts           # Proxy config to FastAPI backend
├── docker-compose.yml
├── .gitignore
└── README.md
```

---

## Running the Application

### 1. Start the Backend API
From `ai-email-copilot/backend`:
```powershell
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

- **Swagger Documentation:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check:** [http://localhost:8000/health](http://localhost:8000/health)
- **Built Frontend Web UI:** [http://localhost:8000/app/](http://localhost:8000/app/)

### 2. Start the Frontend in Development Mode (with Live Reload)
From `ai-email-copilot/frontend`:
```powershell
npm run dev
```
Open [http://localhost:5173](http://localhost:5173). API requests are automatically proxied to `http://localhost:8000`.

### 3. Run Automated Tests
From `ai-email-copilot/backend`:
```powershell
$env:PYTHONPATH="app;."
pytest tests -v
```

---

## Human-in-the-Loop Safeguard Guarantee

1. **State Machine:** Reply drafts start strictly in `pending` status.
2. **Explicit Confirmation:** The `/api/v1/replies/{id}/approve` endpoint rejects any request without `{"confirm": true}`.
3. **Idempotency & Double-Send Prevention:** Once a draft transitions to `sending` or `sent`, duplicate attempts are immediately blocked with HTTP 400/409.
---

## Real-Time Push Notifications & Background Sync

1. **Google Cloud Pub/Sub Webhook (`POST /api/v1/emails/webhook/pubsub`):**
   * Receives push notifications from Google Cloud Pub/Sub.
   * Decodes Base64 message payloads containing `emailAddress` and `historyId`.
   * Incrementally synchronizes mailbox changes using Gmail cursor tokens (`startHistoryId`).

2. **Watch Registration (`POST /api/v1/emails/watch`):**
   * Calls Gmail's `users.watch` API with your Pub/Sub topic to enable push updates.

3. **Background Recovery Worker (`app/workers/email_worker.py`):**
   * Scans active connections and reconciles missed notifications or renewing watches expiring within 24h.
   * Run standalone: `python -m app.workers.email_worker`

4. **Data Minimization & Account Purge (`DELETE /api/v1/auth/user/delete-data`):**
   * Revokes remote OAuth grants and cascades full deletion of all user emails, analyses, tasks, drafts, and sync states from PostgreSQL.


