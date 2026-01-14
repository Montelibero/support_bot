IMAGE_NAME := "support_bot"

# Default target
default:
    @just --list

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
