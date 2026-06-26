.PHONY: install playground run test

install:
	uv sync

playground:
	uv run adk web app --host 127.0.0.1 --port 18081

run:
	uv run uvicorn app.agent_runtime_app:app --host 0.0.0.0 --port 8080

test:
	uv run pytest tests/unit tests/integration -v
