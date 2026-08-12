"""Behavioral tests for the criticality classifier."""

from __future__ import annotations

from pathlib import Path

from arbiter.rules import classify, load_rules_file, load_rules_yaml, path_matches


EXAMPLE = Path(__file__).resolve().parents[1] / "arbiter.rules.yaml.example"


def test_path_glob_auth_nested() -> None:
    assert path_matches("src/auth/login.py", "**/auth/**")
    assert path_matches("auth/login.py", "**/auth/**")


def test_path_glob_infra_anchored() -> None:
    assert path_matches("infra/tf/main.tf", "infra/**")
    assert not path_matches("x/infra/tf", "infra/**")


def test_path_glob_absolute_matches_relative_scope() -> None:
    """OpenCode apply_patch sends absolute paths; plans use auth/**."""
    abs_path = "/private/tmp/arbiter-podman/project/auth/handler.py"
    assert path_matches(abs_path, "auth/**")
    assert path_matches(abs_path, "auth/handler.py")
    assert not path_matches(abs_path, "billing/**")
    # Relative paths stay anchored — nested infra under x/ must not match.
    assert not path_matches("x/infra/tf", "infra/**")


def test_path_glob_dockerfile_and_schema() -> None:
    assert path_matches("app/Dockerfile", "**/Dockerfile")
    assert path_matches("Dockerfile", "**/Dockerfile")
    assert path_matches("db/user_schema.py", "**/*_schema.py")


def test_path_glob_github_workflows() -> None:
    assert path_matches(".github/workflows/ci.yml", ".github/workflows/**")


def test_load_example_rules() -> None:
    rules = load_rules_yaml(EXAMPLE.read_text(encoding="utf-8"))
    assert rules["default"] == "routine"
    assert "**/auth/**" in rules["critical"]["paths"]
    assert {"deletes_files": True} in rules["critical"]["any_of"]


def test_missing_rules_file_is_fail_closed(tmp_path: Path) -> None:
    assert load_rules_file(tmp_path / "missing.yaml") is None
    result = classify({"paths": ["readme.md"]}, None)
    assert result.criticality == "critical"
    assert "no rules file" in result.reason


def test_no_paths_declared_is_critical() -> None:
    rules = load_rules_yaml(EXAMPLE.read_text(encoding="utf-8"))
    result = classify({"diff": "x"}, rules)
    assert result.criticality == "critical"
    assert result.reason == "no paths declared"


def test_empty_paths_is_critical() -> None:
    rules = load_rules_yaml(EXAMPLE.read_text(encoding="utf-8"))
    result = classify({"paths": []}, rules)
    assert result.criticality == "critical"
    assert result.reason == "no paths declared"


def test_invalid_rules_structure_is_fail_closed() -> None:
    result = classify({"paths": ["docs/x.md"]}, {"critical": "oops", "default": "routine"})
    assert result.criticality == "critical"
    assert result.reason == "invalid rules"


def test_unreadable_rules_file_is_fail_closed(tmp_path: Path) -> None:
    bad = tmp_path / "arbiter.rules.yaml"
    bad.write_text("critical: [\n  this is : : broken\n", encoding="utf-8")
    # Parser may return a dict or raise; load_rules_file must not yield a
    # permissive classifier either way.
    loaded = load_rules_file(bad)
    result = classify({"paths": ["docs/x.md"]}, loaded)
    assert result.criticality == "critical"


def test_critical_path_match() -> None:
    rules = load_rules_yaml(EXAMPLE.read_text(encoding="utf-8"))
    result = classify({"paths": ["svc/auth/tokens.py"]}, rules)
    assert result.criticality == "critical"


def test_any_of_flag() -> None:
    rules = load_rules_yaml(EXAMPLE.read_text(encoding="utf-8"))
    result = classify(
        {"paths": ["docs/readme.md"], "deletes_files": True},
        rules,
    )
    assert result.criticality == "critical"


def test_default_routine() -> None:
    rules = load_rules_yaml(EXAMPLE.read_text(encoding="utf-8"))
    result = classify({"paths": ["docs/readme.md"]}, rules)
    assert result.criticality == "routine"
