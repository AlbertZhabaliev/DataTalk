# DataTalk

Natural-language to SQL query interface for multiple database engines. Ask questions about your data in plain English and get formatted tables, charts, and explanations — no SQL required.

## Features

- **Natural-language queries** — powered by OpenAI, Anthropic, or Ollama LLMs
- **Multi-engine support** — PostgreSQL, MSSQL, ClickHouse, Oracle
- **Voice input** — record questions with your microphone (uses faster-whisper)
- **Interactive charts** — bar, line, area, pie, scatter — configurable per query
- **Saved queries** — persist, favourite, rerun, and export queries to CSV
- **Dashboard** — pin charts from saved queries onto a live dashboard
- **Schema browser** — explore tables, columns, and preview data
- **SQL guardrails** — read-only enforcement via sqlglot AST validation

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+, FastAPI |
| Frontend | NiceGUI (Quasar/Quasar-based reactive UI) |
| LLM orchestration | LangChain 0.2 |
| Database drivers | SQLAlchemy 2.0 with psycopg, pyodbc, clickhouse-sqlalchemy, oracledb |
| Voice | faster-whisper (runs locally via CPU) |
| Charts | Apache ECharts |
| Persistence | YAML files (no external DB required) |

## Quick start

### Prerequisites

- Python 3.12+
- A database (PostgreSQL / MSSQL / ClickHouse / Oracle) with read-only access
- An LLM API key (OpenAI, Anthropic, or a local Ollama instance)

### Setup

```bash
# Clone
git clone https://github.com/AlbertZhabaliev/DataTalk.git
cd DataTalk

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate    # Windows
source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Configure databases and LLM provider
# Option A: Copy the example config and edit
cp app/data/app_config.example.yaml app/data/app_config.yaml
#   Then edit app/data/app_config.yaml with your LLM key and database settings
# Option B: Configure via the Settings UI after launching

# Start the app
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

### Using the Settings UI

1. Go to **Settings** → **AI Provider** → enter your provider (OpenAI/Anthropic/Ollama), model name, and API key
2. Under **Database Connections**, add your database (or edit `app/data/app_config.yaml` directly)
3. Click **Test** to verify each connection, then ask a question on the **Ask** page

## Configuration

Configuration is stored in `app/data/app_config.yaml` and can be edited at runtime via the Settings UI. API keys and credentials are only stored in this file — never hardcoded.

See `app/data/app_config.example.yaml` for a full reference.

## Project structure

```
app/
├── main.py                  # FastAPI entry point + prewarming
├── frontend.py              # NiceGUI UI (Ask, Browse, Saved, Dashboard, Settings)
├── api/                     # REST API routes
│   ├── routes_query.py      # POST /api/query
│   ├── routes_schema.py     # GET /api/databases, /api/schema/...
│   ├── routes_saved.py      # CRUD for saved queries
│   ├── routes_voice.py      # POST /api/voice, /api/transcribe
│   └── routes_config.py     # GET/PUT for LLM, databases, glossary
├── core/
│   ├── pipeline.py          # NL→SQL pipeline orchestrator
│   ├── guardrails.py        # SQL read-only validator
│   ├── config_store.py      # Thread-safe YAML config store
│   ├── saved_store.py       # Thread-safe saved-query store
│   ├── executor/            # SQLAlchemy executor layer
│   ├── schema/              # Schema introspection + glossary
│   ├── llm/                 # LangChain SQL generation + report
│   └── results/             # Dashboard HTML generator
├── config/
│   ├── settings.py          # Pydantic settings
│   └── connections.py       # Executor registry (lazy connection pool)
├── models/
│   └── request.py           # Pydantic request/response models
└── data/
    ├── app_config.yaml      # Runtime config (gitignored — contains secrets)
    ├── app_config.example.yaml
    ├── saved_queries.yaml   # Saved queries + chart configs
    └── .env.example
```

## Security

- All SQL is validated as read-only by sqlglot before execution
- API keys and database credentials live only in `app/data/app_config.yaml` (excluded from git)
- Database connections use `read_only: true` by default
