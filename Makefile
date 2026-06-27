DATASET ?= data/memorae_mock_events.json

.PHONY: install run test example lint

install:
	poetry install

run: install
	poetry run memorae-memory --dataset $(DATASET)

example: install
	poetry run memorae-memory --dataset $(DATASET) --output outputs/example_run.json

test: install
	poetry run python -m unittest

lint: install
	poetry run ruff check src tests
