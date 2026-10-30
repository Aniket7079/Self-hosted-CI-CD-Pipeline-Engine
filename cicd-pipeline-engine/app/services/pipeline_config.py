"""
Per-repository pipeline configuration, read from `.cicd.yml` at the repo root.

Example .cicd.yml:
    test_command: "pytest -q"        # run inside the built image
    container_name: "my-service"     # name used on the deploy server
    port_map: "8000:8000"            # host:container port mapping on deploy

Every key is optional; missing/invalid files fall back to safe defaults so a
repo without a .cicd.yml still builds and tests.
"""
from __future__ import annotations
import os
import yaml

DEFAULTS = {
    "test_command": "pytest --tb=short -v",
    "container_name": None,      # None -> derived from the pipeline name
    "port_map": "8000:8000",
}
_ALLOWED = set(DEFAULTS)


def load_pipeline_config(repo_dir: str) -> dict:
    """Return DEFAULTS overlaid with values from <repo_dir>/.cicd.yml."""
    cfg = dict(DEFAULTS)
    path = os.path.join(repo_dir, ".cicd.yml")
    if not os.path.isfile(path):
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(f".cicd.yml is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(".cicd.yml must contain a mapping of settings")
    unknown = set(data) - _ALLOWED
    if unknown:
        raise ValueError(
            f".cicd.yml has unknown keys: {sorted(unknown)} (allowed: {sorted(_ALLOWED)})"
        )
    for k, v in data.items():
        if v is not None:
            cfg[k] = str(v)
    return cfg
