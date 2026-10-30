"""Tests for .cicd.yml loading."""
import pytest
from app.services.pipeline_config import load_pipeline_config, DEFAULTS


def test_defaults_when_no_file(tmp_path):
    assert load_pipeline_config(str(tmp_path)) == DEFAULTS


def test_overrides_from_file(tmp_path):
    (tmp_path / ".cicd.yml").write_text(
        'test_command: "pytest -q"\ncontainer_name: my-svc\nport_map: "9000:8000"\n'
    )
    cfg = load_pipeline_config(str(tmp_path))
    assert cfg["test_command"] == "pytest -q"
    assert cfg["container_name"] == "my-svc"
    assert cfg["port_map"] == "9000:8000"


def test_partial_file_keeps_other_defaults(tmp_path):
    (tmp_path / ".cicd.yml").write_text("test_command: make test\n")
    cfg = load_pipeline_config(str(tmp_path))
    assert cfg["test_command"] == "make test"
    assert cfg["port_map"] == DEFAULTS["port_map"]


def test_empty_file_uses_defaults(tmp_path):
    (tmp_path / ".cicd.yml").write_text("")
    assert load_pipeline_config(str(tmp_path)) == DEFAULTS


def test_unknown_key_rejected_with_clear_message(tmp_path):
    (tmp_path / ".cicd.yml").write_text("test_comand: typo\n")   # typo'd key
    with pytest.raises(ValueError, match="unknown keys"):
        load_pipeline_config(str(tmp_path))


def test_invalid_yaml_rejected(tmp_path):
    (tmp_path / ".cicd.yml").write_text("test_command: [unclosed\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        load_pipeline_config(str(tmp_path))


def test_non_mapping_rejected(tmp_path):
    (tmp_path / ".cicd.yml").write_text("- just\n- a list\n")
    with pytest.raises(ValueError, match="mapping"):
        load_pipeline_config(str(tmp_path))
