
default: lint mypy test

UV_RUN := "uv run --"

ruff-format:
	{{UV_RUN}} prek run -a ruff-format

ruff-check:
	{{UV_RUN}} ruff check

ruff-fix:
	{{UV_RUN}} prek run -a ruff-check

fix: ruff-format ruff-fix

mypy:
	-{{UV_RUN}} prek run -a mypy

lint: mypy ruff-check

test:
	{{UV_RUN}} python -m  pytest --cov=src --cov-config=.coveragerc
