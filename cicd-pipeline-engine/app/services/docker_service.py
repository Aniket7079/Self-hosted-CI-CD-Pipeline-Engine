"""
Docker build and run service.
Uses the Docker Python SDK to build images and run test containers.
All streaming is done via generator to avoid blocking the event loop.
"""
import os
import json
import logging
from typing import Generator
from app.core.config import settings

log = logging.getLogger(__name__)


def get_docker_client():
    try:
        import docker
        return docker.from_env()
    except Exception as e:
        raise RuntimeError(f"Docker not available: {e}")


def build_image(
    context_path : str,
    image_tag    : str,
    dockerfile   : str = "Dockerfile",
) -> Generator[str, None, None]:
    """
    Build a Docker image and yield log lines as they stream.
    This is a blocking generator — run in a thread pool in async context.

    Yields: log line strings (stdout from docker build)
    Raises: RuntimeError if build fails
    """
    client = get_docker_client()
    try:
        log.info("Building Docker image: %s from %s", image_tag, context_path)
        build_logs = client.api.build(
            path       = context_path,
            tag        = image_tag,
            dockerfile = dockerfile,
            rm         = True,         # remove intermediate containers
            decode     = True,         # parse JSON log lines automatically
        )
        for chunk in build_logs:
            if "stream" in chunk:
                line = chunk["stream"].rstrip("\n")
                if line:
                    yield line
            elif "error" in chunk:
                raise RuntimeError(f"Docker build error: {chunk['error']}")
            elif "status" in chunk:
                yield f"[push] {chunk['status']}"

        log.info("Docker image built successfully: %s", image_tag)

    except Exception as e:
        log.error("Docker build failed: %s", e)
        raise
    finally:
        client.close()


def run_tests_in_container(
    image_tag  : str,
    test_cmd   : str = "pytest --tb=short",
) -> tuple[int, list[str]]:
    """
    Run the test command inside the built Docker image.
    Returns (exit_code, list_of_log_lines).
    exit_code 0 = tests passed.
    """
    client = get_docker_client()
    logs_out = []
    try:
        container = client.containers.run(
            image    = image_tag,
            command  = test_cmd,
            detach   = True,
            remove   = False,
            mem_limit= "512m",
        )
        # Stream logs
        for line in container.logs(stream=True, follow=True):
            decoded = line.decode("utf-8", errors="replace").rstrip()
            logs_out.append(decoded)

        result    = container.wait()
        exit_code = result.get("StatusCode", 1)
        container.remove(force=True)
        return exit_code, logs_out

    except Exception as e:
        return 1, [f"Container run failed: {str(e)}"]
    finally:
        client.close()


def push_image(image_tag: str) -> Generator[str, None, None]:
    """Push image to Docker registry. Yields status lines."""
    if not settings.DOCKER_USERNAME:
        yield "[skip] No Docker registry credentials configured"
        return
    client = get_docker_client()
    try:
        client.login(
            username=settings.DOCKER_USERNAME,
            password=settings.DOCKER_PASSWORD,
            registry=settings.DOCKER_REGISTRY,
        )
        for line in client.images.push(image_tag, stream=True, decode=True):
            if "status" in line:
                yield f"[push] {line['status']}"
            if "error" in line:
                raise RuntimeError(line["error"])
    finally:
        client.close()
