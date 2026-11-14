.PHONY: install run seed reset clean

install:
	python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt

run:
	.venv/bin/flask --app run.py run --host 0.0.0.0 --port 5055 --debug

seed:
	.venv/bin/flask --app run.py seed --reset

reset:
	.venv/bin/flask --app run.py reset-db --yes

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
