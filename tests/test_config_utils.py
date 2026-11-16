from src.config import deep_merge, load_config


def test_deep_merge_nested():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    merged = deep_merge(base, {"a": {"b": 9}})
    assert merged == {"a": {"b": 9, "c": 2}, "d": 3}
    assert base["a"]["b"] == 1


def test_load_config_defaults_when_no_file():
    config = load_config()
    assert config["stage2"]["fill_markers"] == ["____"]
    assert config["stage2"]["max_validation_rounds"] == 2
    assert config["llm"]["max_retries"] == 3


def test_load_config_partial_override(tmp_path):
    cfg_file = tmp_path / "custom.yaml"
    cfg_file.write_text("stage2:\n  total_minutes: 90\n", encoding="utf-8")
    config = load_config(cfg_file)
    assert config["stage2"]["total_minutes"] == 90
    assert config["stage2"]["fill_markers"] == ["____"]
    assert config["llm"]["temperature"] == 0.3


def test_config_yaml_in_repo_is_valid():
    from pathlib import Path

    repo_config = Path(__file__).resolve().parent.parent / "config.yaml"
    config = load_config(repo_config)
    assert "llm" in config and "stage1" in config and "stage2" in config
    assert config["stage1"]["chapter_pattern"]
    assert config["stage2"]["total_minutes"] == 45
