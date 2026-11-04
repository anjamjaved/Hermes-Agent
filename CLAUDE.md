# Hermes-4gent — AI Agent Framework

Python FastAPI + TypeScript frontend. Self-improving agent with toolsets system.

## Stack
- **Backend**: Python 3.11+, FastAPI, uv
- **Frontend**: Vite + TypeScript
- **Workspace**: npm workspaces (apps/*, ui-tui, web, tests-js)

## Key Commands
- `uv run python run_agent.py` — start the agent
- `npm run check` — lint all workspaces
- `pytest` — full test suite (skips browser tests)
- `pytest -m browser` — run browser integration tests

## Conventions
- Exact-pinned Python deps in pyproject.toml (no ranges)
- No npm or Python deps without explicit approval
- See `/media/abelion/Isaf/ican/project/CLAUDE.md` for shared project rules
