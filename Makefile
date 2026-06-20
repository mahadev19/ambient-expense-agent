.PHONY: install playground run generate-traces grade

install:
	agents-cli install

playground:
	agents-cli playground

run:
	uv run uvicorn expense_agent.fast_api_app:app --host 127.0.0.1 --port 8080

generate-traces:
	uv run python tests/eval/generate_traces.py

grade:
	agents-cli eval grade --traces artifacts/traces/generated_traces.json --config tests/eval/eval_config.yaml


