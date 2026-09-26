.PHONY: install lint format format-check typecheck test test-serial test-cov clean

install:
	pip install -e ".[dev,rag,mcp]"

lint:
	ruff check .

format:
	ruff format .

format-check:
	ruff format --check .

# Checks the whole package, not a hand-listed subset. pyproject's
# [[tool.mypy.overrides]] already relaxes exactly the legacy modules, so passing
# the package is equivalent to the old list *and* picks up a new module
# automatically -- the list form silently skipped anything not named in it.
typecheck:
	python -m mypy repo2graph/

# Parallel by default: the suite is ~1,700 independent tests and `-n auto` takes
# it from ~134s to ~36s on 16 cores. Nothing here shares a database, a port or a
# working directory, so the workers do not contend.
test:
	pytest -q -n auto

# The escape hatch, and the reason `test` is not the only target: under xdist the
# output of a failing test is interleaved with every other worker's and `--pdb`
# cannot attach. Reach for this when you are diagnosing one failure, not when you
# are checking the suite.
test-serial:
	pytest -q

test-cov:
	pytest -n auto --cov=repo2graph --cov-report=term-missing

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
