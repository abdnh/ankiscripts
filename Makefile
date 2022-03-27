.PHONY: all build format checkformat typecheck lint check run clean

all: build

build:
	python -m ankibuild --qt all --install

format:
	python -m black src

checkformat:
	python -m black --diff --color src

typecheck:
	python -m mypy src

lint:
	python -m pylint src

check: lint typecheck checkformat

run: build
	python run.py

clean:
	rm -rf build/
