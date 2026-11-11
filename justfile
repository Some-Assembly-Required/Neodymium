# List available recipes
default:
    @just --list

# --- dev ---

# Install dev dependencies and pre-commit hooks
[group('dev')]
setup:
    pip install -e ".[dev]"
    pre-commit install

# Run the neodymium CLI
[group('dev')]
run *args:
    neodymium {{args}}

# Type-check with mypy and lint with ruff (runs both, fails if either fails)
[group('dev')]
lint:
    #!/usr/bin/env bash
    fail=0
    mypy neodymium || fail=1
    ruff check . || fail=1
    exit $fail

# Format code with ruff
[group('dev')]
fmt:
    ruff format .

# --- testing ---

# Run the test suite (spins up / tears down the ephemeral Mongo automatically)
[group('testing')]
test *args:
    pytest {{args}}

# Run the fake-vendor fixture webserver (e.g. `just test-server --port 8000`)
[group('testing')]
test-server *args:
    python -m tests.fake_vendor {{args}}

# Run the ephemeral, RAM-backed MongoDB used by the tests (Ctrl+C to stop)
[group('testing')]
test-mongo *args:
    docker compose -f tests/docker-compose.yml up mongodb {{args}}

# Run the test FTP server (populate tests/ftp_root first, then `up ftp`)
[group('testing')]
test-ftp *args:
    docker compose -f tests/docker-compose.yml up ftp {{args}}

# --- deployment ---

# Build a Python wheel into dist/
[group('deployment')]
build:
    python -m build --wheel

# Remove build artifacts (build/ and dist/)
[group('deployment')]
clean:
    rm -rf build/ dist/

# Start the database via docker compose (e.g. `just database -d` to run detached)
[group('deployment')]
database *args:
    docker compose --env-file .env -f docker/docker-compose.yml up {{args}}
