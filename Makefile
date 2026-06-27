.PHONY: run test example

run:
	PYTHONPATH=src python -m memorae_memory.main --dataset data/memorae_mock_events.json

example:
	PYTHONPATH=src python -m memorae_memory.main --dataset data/memorae_mock_events.json --output outputs/example_run.json

test:
	PYTHONPATH=src python -m unittest
