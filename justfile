# List available recipes
default:
    @just --list

# Install dev dependencies and pre-commit hooks
setup:
    pip install -e ".[dev]"
    pre-commit install

# Run the neodymium CLI
run *args:
    neodymium {{args}}

# Type-check with mypy and lint with ruff (runs both, fails if either fails)
lint:
    #!/usr/bin/env bash
    fail=0
    mypy neodymium || fail=1
    ruff check . || fail=1
    exit $fail

# Format code with ruff
fmt:
    ruff format .
