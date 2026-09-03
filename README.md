# Requirement Agent

This repository is initialized to match the layered architecture described in the project design document:

- apps/: foreground service entrypoints for API, MCP, and worker jobs
- src/: domain, application, infrastructure, and interface layers
- migrations/: database change scripts and schema evolution notes
- tests/: unit, integration, and end-to-end test packages
- deploy/: runtime and environment configuration

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn apps.api.main:app --reload
```

## Notes

The current scaffold intentionally keeps business logic minimal while following the suggested separation of concerns.
