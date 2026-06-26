# AGENTS.md

This file gives repository-specific guidance for coding agents working in `mcp_stock_server`.

## What This Project Is

`mcp_stock_server` is a Python MCP server for stock-data workflows.

- It serves MCP tools over `stdio` or `streamable-http`.
- It uses MySQL-backed repositories for stock data and task persistence.
- It includes task-aware MCP tools and restart recovery support.

## Working Style

- Think before coding. If behavior is ambiguous, surface the ambiguity instead of silently choosing.
- Prefer the minimum change that solves the request.
- Keep edits surgical. Do not refactor unrelated code.
- Match the existing style, even when it is a little bootstrap-oriented.
- When changing behavior, define how you will verify it before editing.

## Use CodeGraph First

This repo has a `.codegraph/` directory. Use CodeGraph before grep/read when you need to understand code structure or call paths.

Good first targets:

- `create_mcp_server`
- `build_mysql_services`
- `build_stock_tool_registry`
- `ToolDispatcher`
- `MySQLTaskStore`
- `TaskRecoveryCoordinator`

## Main Entry Points

- `main.py`
  - Loads `config.json`
  - Builds services
  - Optionally builds `MySQLTaskStore`
  - Starts the server
- `server.py`
  - Builds the `FastMCP` app
  - Registers tools
  - Wires auth, approvals, policy, audit, manifest, tasks, and recovery
- `run_technical_snapshot.py`
  - CLI entry for the technical snapshot tool
- `init/init_stock_master.py`
  - Bootstraps stock master data
- `init/init_stock_daily.py`
  - Bootstraps historical daily bars

## Architecture

- `tooling/`
  - Tool definitions and registration
  - `build_stock_tool_registry(...)` is the main registration boundary
- `protocol/`
  - Dispatch, response shaping, and error translation
- `services/`
  - Stock-domain business logic
- `repositories/`
  - Persistence and MySQL-facing logic
  - Includes `MySQLTaskStore`
- `models/`
  - Request, response, and DB models
- `auth/`
  - OAuth config, auth context, approvals, elicitation helpers
- `governance/`
  - Policy and redaction
- `audit/`
  - JSONL audit output
- `manifest/`
  - Capability manifest generation
- `recovery.py`
  - Startup recovery coordinator for replayable tasks

## Current Runtime Facts

- Runtime config comes from `config.json`.
- `config.json` is environment-specific and currently contains local development credentials and auth settings.
- Be careful editing it unless the task is explicitly about runtime configuration.
- The checked-in auth settings point at a local Keycloak dev server on `127.0.0.1:8080`.
- `main.py` supports:
  - `python main.py`
  - `python main.py streamable-http`

### Local Keycloak Dev Command

If you need the local auth server used by `config.json`, start Keycloak with:

```bash
docker rm -f keycloak
docker run -d \
  --name keycloak \
  -p 127.0.0.1:8080:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
  -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  -v keycloak_data:/opt/keycloak/data \
  quay.io/keycloak/keycloak start-dev
```

## Active MCP Tools

Do not assume every historical tool in the repo is currently exposed.

`server.py` currently registers these active MCP tools:

- `get_stock_daily_bars`
- `upsert_stock_daily_bars`
- `insert_stock_daily_bars_after_close`
- `get_technical_snapshot`
- `get_capability_manifest`
- `screen_b1_stocks`

There are older indicator tool registrations still commented out in `server.py`. Do not remove or revive them unless the task asks for that specifically.

## Initialization And Data Setup

- Schema files:
  - `db/schema.sql`
  - `db/task_schema.sql`
- Stock master bootstrap:
  - `python init/init_stock_master.py`
- Daily bar bootstrap:
  - `python init/init_stock_daily.py`

Notes:

- Both init scripts use `build_mysql_services(...)`.
- Init scripts use `mootdx` as an upstream market-data source.

## Testing

- The repo uses `unittest`.
- Do not assume `pytest` conventions.

Main test files:

- `tests/test_mcp_stock_server.py`
- `tests/test_mcp_refactor_architecture.py`
- `tests/test_mysql_task_store.py`
- `tests/test_task_recovery.py`

Useful verification commands:

```bash
python -m unittest discover -s tests
python -m unittest tests.test_mcp_stock_server
python -m unittest tests.test_mysql_task_store
python -m unittest tests.test_task_recovery
```

When fixing a bug, prefer:

1. Write or update a focused failing test.
2. Make the minimal code change.
3. Re-run the focused test.
4. Re-run a broader relevant suite.

## Change Boundaries

If you change MCP behavior, inspect at least:

- `server.py`
- `tooling/stock_tools.py`
- `protocol/dispatcher.py`
- affected `services/` and `models/`

If you change task behavior, inspect at least:

- `repositories/task_store.py`
- `recovery.py`
- `tests/test_mysql_task_store.py`
- `tests/test_task_recovery.py`

If you change auth, approvals, or destructive-tool flow, inspect at least:

- `auth/`
- `governance/`
- `audit/`

## Editing Rules For This Repo

- Do not broaden scope into cleanup unless asked.
- Do not remove unrelated dead code you did not create.
- Do not rewrite the config shape unless required.
- Do not replace the layered architecture with shortcut logic in `server.py`.
- Prefer extending existing layers over introducing new abstractions.

## Documentation Notes

- `docs/superpowers/specs/` contains useful design context, but current code is the source of truth.
- When documenting or testing behavior, prefer live entry points and current registrations over older design assumptions.
