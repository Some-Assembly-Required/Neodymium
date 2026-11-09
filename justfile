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

# Run the test suite (spins up / tears down the ephemeral Mongo automatically)
test *args:
    pytest {{args}}

# Run the fake-vendor fixture webserver (e.g. `just test-server --port 8000`)
test-server *args:
    python -m tests.fake_vendor {{args}}

# Run the ephemeral, RAM-backed MongoDB used by the tests (Ctrl+C to stop)
test-mongo *args:
    docker compose -f tests/docker-compose.yml up {{args}}

# Start the database via docker compose
database:
    docker compose --env-file .env -f docker/docker-compose.yml up
