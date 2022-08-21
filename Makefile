.PHONY: all build fix mypy pylint run clean

all: build

build:
	python -m ankibuild --qt all --exclude user_files/**/

fix:
	python -m black src --exclude="forms|vendor"
	python -m isort src

mypy:
	python -m mypy .

pylint:
	python -m pylint src tests

run: build
	python run.py

clean:
	rm -rf build/
