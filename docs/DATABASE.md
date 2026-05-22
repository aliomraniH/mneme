# Database — shared Helium Postgres

Both `mneme` and `saaz` connect to the same Replit-hosted Postgres:

```
postgresql://postgres:<password>@helium/heliumdb?sslmode=disable
```

This document explains how the two projects coexist in one database, what each writes, and how to sanity-check the layout.

## Connection notes

- **`helium` is an internal Replit hostname.** It only resolves from inside a Replit deployment or workspace. You cannot connect from your laptop without a tunnel — don't try.
- **`sslmode=disable` is intentional** because the connection is over Replit's internal network. If you ever move this Postgres off Replit, switch to `sslmode=require`.
- **One Postgres, two projects.** Don't create separate databases or schemas. Both projects use the `public` schema and rely on table-name discipline.

## Table ownership

| Owner | Tables | Notes |
|---|---|---|
| `saaz` | `artist`, `artist_link`, `artist_image`, `song`, `enrichment_run`, `data_provenance` | Domain data. Written by the saaz scripts and the weekly refresh job. |
| `mneme` | `db_schema_snapshot`, `column_doc`, `query_episode`, `expertise_note`, `cache_event` | Memory data. Written only by the mneme middleware. |
| LangGraph | `store`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` | Auto-created by `AsyncPostgresStore.setup()` and `AsyncPostgresSaver.setup()`. Don't touch. |

mneme **never writes to** any saaz table. saaz **never writes to** any mneme table. Discipline, not enforcement — there's only one DB role for the MVP.

## Roles and least privilege

For the MVP we use the default `postgres` role for everything because Replit Helium ships that way. The first thing to do once anything ships to a real user:

1. Create a `mneme_ro` role with `GRANT SELECT` on the saaz tables and full CRUD on the mneme tables.
2. Switch mneme's `DATABASE_URL` to `mneme_ro`.
3. Keep saaz running as `postgres` (it needs write access to its own tables and the audit/provenance tables).

```sql
-- Future hardening. Don't run this for the MVP.
CREATE ROLE mneme_ro LOGIN PASSWORD '<random>';
GRANT SELECT ON artist, artist_link, artist_image, song,
                 enrichment_run, data_provenance
      TO mneme_ro;
GRANT ALL    ON db_schema_snapshot, column_doc, query_episode,
                 expertise_note, cache_event, store, checkpoints,
                 checkpoint_blobs, checkpoint_writes, checkpoint_migrations
      TO mneme_ro;
```

## Sanity-checking saaz (from inside the mneme repo)

When you want to confirm saaz is ready for mneme to observe queries against it:

```sql
-- artists landed?
SELECT genre, count(*) AS n FROM artist GROUP BY genre ORDER BY n DESC;

-- bios filled in?
SELECT bio_source, count(*) AS n FROM artist GROUP BY bio_source;

-- embeddings ready?
SELECT count(*) AS with_embedding FROM artist WHERE embedding IS NOT NULL;

-- provenance covered?
SELECT count(*) FROM data_provenance;

-- pgvector working?
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
```

Expected outputs for a healthy saaz:
- ~30 artists across 3-4 genres
- bio_source breakdown: `wikipedia` dominant, with some `seed`, `perplexity`, and `anthropic_web`
- at least 25 rows with `embedding IS NOT NULL`
- at least 30 rows in `data_provenance`
- `vector` extension present

## Sanity-checking mneme (from inside the saaz repo)

You generally don't need to, but if you want to confirm mneme is observing queries:

```sql
-- mneme writing audit rows?
SELECT count(*) AS audited_calls FROM query_episode;

-- broken down by namespace
SELECT db_namespace, count(*) FROM query_episode
GROUP BY db_namespace ORDER BY count(*) DESC;

-- broken down by tool
SELECT tool_name, count(*) FROM query_episode
GROUP BY tool_name ORDER BY count(*) DESC LIMIT 10;
```

If `query_episode` has rows and the `db_namespace` column shows `saaz_demo` for queries against the artist tables, the router is working.

## How `db_namespace` maps to saaz

In mneme's `routing.py` (Phase 1), the rule for routing to the saaz namespace is keyword-based:

```python
# mneme/agent_service/routing.py — Phase 1 sketch
SAAZ_KEYWORDS = ("artist", "song", "persian", "jazz", "saaz")

def route_to_namespace(tool_name: str, params: dict) -> str:
    blob = (tool_name + " " + json.dumps(params or {})).lower()
    if any(k in blob for k in SAAZ_KEYWORDS):
        return "saaz_demo"
    if "pinecone" in blob or "vector" in blob:
        return "pinecone_main"
    if "postgres" in blob or "sql" in blob:
        return "pg_main"
    return "default"
```

This is intentionally crude. Phase 2 swaps in a tiny LLM router for ambiguous cases. The contract — `tool_name + params → db_namespace: str` — stays the same.

## Recovery scenarios

**"I accidentally dropped a mneme table."** Re-run `make migrate`. The mneme tables are recreated empty; you lose memory but not domain data.

**"I accidentally dropped a saaz table."** From the saaz repo: `make migrate && make seed && make enrich && make embed`. ~10 minutes, ~$0.50.

**"The Helium password rotated."** Update `DATABASE_URL` in both Replits' Secrets. The internal hostname `helium` stays the same.

**"I need to reset memory but keep saaz data."** From mneme:

```sql
TRUNCATE query_episode, expertise_note, cache_event,
         db_schema_snapshot, column_doc;
TRUNCATE store, checkpoints, checkpoint_blobs, checkpoint_writes;
```

The saaz tables are untouched.
