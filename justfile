IMAGE_NAME := "support_bot"

# Default target
default:
    @just --list

# Quality targets (AI-first bootstrap)
test *args="":
    uv run --group dev pytest {{args}}

test-fast:
    uv run --group dev pytest -q tests/test_customization.py tests/test_webhook_updates.py

lint:
    uv run --group dev ruff check bot/customizations main.py tests/test_customization.py tests/test_webhook_updates.py tests/test_startup_error.py

fmt:
    uv run --group dev ruff format bot/customizations main.py tests/test_customization.py tests/test_webhook_updates.py tests/test_startup_error.py

types:
    uv run --group dev pyright bot/customizations main.py tests/test_customization.py tests/test_webhook_updates.py tests/test_startup_error.py

arch-test:
    @uv run python -c "import pathlib,sys; req=['AGENTS.md','docs/architecture.md','docs/conventions.md','docs/golden-principles.md','docs/glossary.md','docs/exec-plans/active/_template.md','adr/README.md','.linters/README.md']; miss=[p for p in req if not pathlib.Path(p).exists()]; print('arch-test: OK' if not miss else 'arch-test: missing -> ' + ', '.join(miss)); sys.exit(0 if not miss else 1)"

check:
    uv run --group dev ruff format --check bot/customizations main.py tests/test_customization.py tests/test_webhook_updates.py tests/test_startup_error.py && just lint && just types && just test-fast

# Docker targets
build tag="latest":
    # Build Docker image
    docker build -t {{IMAGE_NAME}}:{{tag}} .

run:
    # Build and Run Docker container
    docker build -t {{IMAGE_NAME}}:local .
    # echo "http://127.0.0.1:8081"
    # docker run --rm -p 8081:80 {{IMAGE_NAME}}:local


stop:
    # Stop Docker container
    docker-compose down

rebuild:
    # Rebuild and restart the docker container
    docker-compose build --no-cache && docker-compose up -d --force-recreate

logs:
    # View container logs
    docker-compose logs -f

shell:
    # Open a shell into the running container
    docker-compose exec viewer-caddy sh

# Cleanup targets


clean:
    # Find and remove all __pycache__ directories
    find . -type d -name "__pycache__" -exec rm -rf {} +
    # Find and remove all .log files
    find . -type f -name "*.log" -exec rm -f {} +


push-gitdocker tag="latest":
    docker build -t {{IMAGE_NAME}}:{{tag}} .
    docker tag {{IMAGE_NAME}} ghcr.io/montelibero/{{IMAGE_NAME}}:{{tag}}
    docker push ghcr.io/montelibero/{{IMAGE_NAME}}:{{tag}}
