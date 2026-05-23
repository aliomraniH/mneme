# Replit Agent Prompt — Add a New Database to mneme

Paste the block below directly into the Replit Agent chat.
Replace every `<PLACEHOLDER>` before sending.

---

## Prompt

```
I need you to add a new Postgres database to the mneme MCP middleware and
seed it with a small test dataset.  Follow every step below exactly.
Read docs/ARCHITECTURE.md and CLAUDE.md first so you understand the
constraints.

---

### What mneme is

mneme is a FastMCP middleware server (agent_service/server.py, port 5000)
that proxies one or more upstream "DB MCP servers" to Claude Code.
Each upstream is a running instance of db_mcp/server.py — a generic
read-only Postgres MCP server configured entirely by three env vars:

  DB_MCP_TOOL_PREFIX        →  prefix for all four tools (e.g. "lib")
  DB_MCP_NAME               →  human-readable label
  DB_MCP_DATABASE_URL_ENV   →  name of the env var that holds the DSN

No new Python file is ever written per database.  db_mcp/server.py is the
only binary.

---

### Step 1 — Provision a new Postgres database

Use Replit's database tooling to create a new Postgres database.
Call it  <DB_DISPLAY_NAME>  (e.g. "library-demo").

After creation, save the connection string to Replit Secrets as:
  DATABASE_URL_<DB_SECRET_SUFFIX>
  (e.g. DATABASE_URL_LIBRARY_DEMO)

If Replit provisions a Neon database, the connection string will look like:
  postgresql://<user>:<pass>@<host>/neondb?sslmode=require

---

### Step 2 — Create the schema and seed test data

Connect to the new database using psql or a migration script and run the
following SQL.  Keep the schema simple — 2-3 tables, ~20-50 rows total.
The goal is just enough data to verify all four db_mcp tools work.

Example for a "library" dataset (adapt to your domain):

```sql
-- Schema
CREATE TABLE IF NOT EXISTS book (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    author      TEXT NOT NULL,
    genre       TEXT NOT NULL,
    year        INT,
    available   BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS member (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    joined_at   DATE DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS loan (
    id          SERIAL PRIMARY KEY,
    book_id     INT REFERENCES book(id),
    member_id   INT REFERENCES member(id),
    loaned_at   DATE DEFAULT CURRENT_DATE,
    returned_at DATE
);

-- Seed data
INSERT INTO book (title, author, genre, year) VALUES
  ('Dune',                     'Frank Herbert',      'sci-fi',    1965),
  ('Foundation',               'Isaac Asimov',       'sci-fi',    1951),
  ('Neuromancer',              'William Gibson',     'cyberpunk', 1984),
  ('The Left Hand of Darkness','Ursula K. Le Guin',  'sci-fi',    1969),
  ('Blindsight',               'Peter Watts',        'sci-fi',    2006),
  ('Piranesi',                 'Susanna Clarke',     'fantasy',   2020),
  ('The Name of the Wind',     'Patrick Rothfuss',   'fantasy',   2007),
  ('Ancillary Justice',        'Ann Leckie',         'sci-fi',    2013),
  ('The Dispossessed',         'Ursula K. Le Guin',  'sci-fi',    1974),
  ('Exhalation',               'Ted Chiang',         'sci-fi',    2019);

INSERT INTO member (name, email) VALUES
  ('Alice Chen',    'alice@example.com'),
  ('Bob Martínez',  'bob@example.com'),
  ('Cleo Osei',     'cleo@example.com'),
  ('Dan Novak',     'dan@example.com'),
  ('Eva Rossi',     'eva@example.com');

INSERT INTO loan (book_id, member_id, loaned_at, returned_at) VALUES
  (1, 1, '2026-05-01', '2026-05-14'),
  (2, 2, '2026-05-10', NULL),
  (3, 3, '2026-05-15', NULL),
  (5, 1, '2026-05-18', NULL);
```

Verify the seed worked:
  SELECT COUNT(*) FROM book;    -- expect 10
  SELECT COUNT(*) FROM member;  -- expect 5
  SELECT COUNT(*) FROM loan;    -- expect 4

---

### Step 3 — Add a .replit workflow task for the new DB MCP server

Open .replit and add a new workflow task inside the existing "Project"
parallel workflow (alongside "Start application" and "Start neon-mcp").

Choose an unused port (e.g. 3001).  Replace <PREFIX>, <NAME>, <SECRET>:

```toml
[[workflows.workflow.tasks]]
task = "workflow.run"
args = "Start <NAME>-mcp"

[[workflows.workflow]]
name = "Start <NAME>-mcp"
author = "agent"

[[workflows.workflow.tasks]]
task = "shell.exec"
args = "DB_MCP_TOOL_PREFIX=<PREFIX> DB_MCP_NAME=<NAME> DB_MCP_DATABASE_URL_ENV=DATABASE_URL_<SECRET> uvicorn db_mcp.server:app --host 0.0.0.0 --port <PORT>"
waitForPort = <PORT>

[workflows.workflow.metadata]
outputType = "console"

[[ports]]
localPort = <PORT>
externalPort = <EXTERNAL_PORT>
```

Example values for a library dataset:
  PREFIX   = lib
  NAME     = library-demo
  SECRET   = LIBRARY_DEMO
  PORT     = 3001
  EXTERNAL_PORT = 3003

---

### Step 4 — Register the new namespace in mneme env vars

In .replit [userenv.shared], update two variables.

UPSTREAM_DB_MCP_SERVERS — add the new entry:
  "lib_demo": "http://localhost:<PORT>/mcp/"

NAMESPACE_ROUTING_KEYWORDS — add routing keywords for the new namespace.
Choose words that appear in table/column names or typical SQL for this DB:
  "lib_demo": ["lib_", "book", "loan", "member", "library", "genre", "author"]

The full updated values should look like:
  UPSTREAM_DB_MCP_SERVERS = "{
    \"saaz_demo\": \"https://saaz-aloomrani.replit.app/mcp\",
    \"neon_purple_kite\": \"http://localhost:3000/mcp/\",
    \"lib_demo\": \"http://localhost:3001/mcp/\"
  }"

  NAMESPACE_ROUTING_KEYWORDS = "{
    \"saaz_demo\": [...existing...],
    \"neon_purple_kite\": [...existing...],
    \"lib_demo\": [\"lib_\", \"book\", \"loan\", \"member\", \"library\", \"genre\", \"author\"]
  }"

---

### Step 5 — Restart and verify

1. Click Run (or restart the Replit project) so all three workflows start.

2. Confirm the new DB MCP server is healthy:
   curl http://localhost:<PORT>/healthz
   Expected: {"status":"ok","name":"library-demo","prefix":"lib"}

3. Confirm mneme exposes the new tools.  Call tools/list on the mneme
   server and check that these four appear:
     lib_list_tables
     lib_describe_table
     lib_query
     lib_stats

4. Run a smoke query through mneme:
   Tool: lib_query
   Args: {"sql": "SELECT genre, COUNT(*) AS n FROM book GROUP BY genre ORDER BY n DESC"}
   Expected: rows with genre and count.

5. Verify routing — after the query, check the audit table:
   SELECT db_namespace, tool_name FROM query_episode
   WHERE tool_name LIKE 'lib_%'
   ORDER BY ts DESC LIMIT 5;
   All rows should show db_namespace = 'lib_demo'.

6. Verify DML is blocked:
   Tool: lib_query
   Args: {"sql": "DELETE FROM book WHERE id = 1"}
   Expected: error — "Only SELECT (or WITH … SELECT) statements are allowed."

---

### Step 6 — Register in the mneme DB registry (optional but recommended)

After the server is running, call the register_database tool through mneme
so the new namespace survives config-file changes and appears in the
list_registered_databases audit trail:

  Tool: register_database
  Args:
    namespace: "lib_demo"
    mcp_url: "http://localhost:3001/mcp/"
    tool_prefix: "lib"
    routing_keywords: ["lib_", "book", "loan", "member", "library"]
    description: "Library demo dataset — books, members, loans"

---

### Constraints to respect (from CLAUDE.md)

- db_mcp/server.py must not be modified — all config is via env vars.
- Do NOT create a new Python server file for the new database.
- The new DB must be read-only from mneme's perspective — no write tools.
- Do not touch the saaz tables (artist, song, etc.) or the mneme memory
  tables (query_episode, expertise_note, etc.).
- New env vars must be added to .env.example with a comment.
- After any code changes run: uv run pytest tests/ -m "not integration" -q
  All tests must still pass before you consider the work done.
```

---

## Checklist (verify before closing the agent)

- [ ] New Postgres DB provisioned and DSN saved to Replit Secrets
- [ ] Schema created and seed data inserted (verify row counts)
- [ ] `.replit` workflow task added (no new Python file)
- [ ] `UPSTREAM_DB_MCP_SERVERS` updated with new namespace
- [ ] `NAMESPACE_ROUTING_KEYWORDS` updated with new keywords
- [ ] `/healthz` on the new DB MCP port returns `"status":"ok"`
- [ ] `tools/list` on mneme shows four `<prefix>_*` tools
- [ ] Smoke query returns data through mneme
- [ ] Audit rows land in `db_namespace = '<namespace>'`
- [ ] DML is rejected by the new tools
- [ ] `register_database` called to persist the entry
- [ ] `uv run pytest tests/ -m "not integration" -q` — all pass
- [ ] `.env.example` updated with `DATABASE_URL_<SECRET>=`
