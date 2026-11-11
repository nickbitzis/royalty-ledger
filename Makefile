PORT ?= 8882
DATA ?= data

.PHONY: install test lint format generate run gate gate-update statement screenshot docker-build docker-run clean

install:
	uv sync --python 3.12

test:
	uv run --python 3.12 --group dev pytest -q

lint:
	uvx ruff check .
	uvx ruff format --check .

format:
	uvx ruff format .
	uvx ruff check . --fix

generate:  ## synthetic catalog and 12 months of reports into $(DATA)
	uv run royaltyledger --data $(DATA) generate

run:
	uv run royaltyledger --data $(DATA) serve --port $(PORT)

gate:  ## what CI runs: fresh data, recompute 2026Q2, fail on any statement change or a cent of difference
	uv run royaltyledger --data data/ci gate --fresh

gate-update:  ## rewrite gate/expected_2026Q2.json after an intended change
	uv run royaltyledger --data data/ci gate --fresh --update

statement:
	uv run royaltyledger --data $(DATA) statement A003 --period 2026Q2

screenshot:  ## needs the server running on $(PORT) and Google Chrome
	uv run python scripts/screenshot.py --port $(PORT)

docker-build:
	docker build -t royalty-ledger .

docker-run:
	docker run --rm -p $(PORT):8882 royalty-ledger

clean:
	rm -rf .pytest_cache .ruff_cache data out
