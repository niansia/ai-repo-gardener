from __future__ import annotations

from pathlib import Path

import pytest
from conftest import write_project
from repo_gardener.analysis import Analyzer
from repo_gardener.reporting import render_pretty


@pytest.mark.parametrize(
    ("config_path", "config_source"),
    [
        ("Dockerfile", 'CMD ["python", "-m", "worker"]'),
        ("compose.yaml", "services:\n  web:\n    command: uvicorn worker:app"),
        ("Procfile", "web: gunicorn worker:app"),
        ("deploy/web.service", "[Service]\nExecStart=/venv/bin/python -m worker"),
        (
            ".github/workflows/run.yml",
            "jobs:\n  run:\n    steps:\n      - run: python -m worker",
        ),
        ("render.yaml", "startCommand: celery -A worker worker"),
        ("tox.ini", "[testenv]\ncommands = pytest --pyargs worker"),
        ("deploy/windows.conf", "command = py -m worker"),
        ("deploy/unbuffered.conf", "command = python -u -m worker"),
    ],
)
def test_deployment_configs_keep_local_runtime_modules_reachable(
    tmp_path: Path, config_path: str, config_source: str
) -> None:
    write_project(
        tmp_path,
        {
            config_path: config_source,
            "worker.py": "def run():\n    return True",
        },
    )

    analyzer = Analyzer(tmp_path)
    report = analyzer.report("scan")

    assert "worker" in analyzer.graph.roots
    assert "worker" in analyzer.graph.reachable
    assert report.metrics["deployment_runtime_references"] == {"worker": [config_path]}


def test_deployment_environment_module_reference_is_a_root(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "Dockerfile": "ENV DJANGO_SETTINGS_MODULE=project.settings",
            "project/settings.py": "DEBUG = False",
        },
    )

    analyzer = Analyzer(tmp_path)

    assert "project.settings" in analyzer.graph.roots
    assert "project.settings" in analyzer.graph.reachable


@pytest.mark.parametrize(
    "source",
    [
        'CMD ["uvicorn", "${APP_MODULE}:app"]',
        "ExecStart=celery -A %CELERY_MODULE% worker",
        "command: gunicorn {{ service.module }}:app",
    ],
)
def test_dynamic_deployment_commands_disable_automatic_deletion(
    tmp_path: Path, source: str
) -> None:
    write_project(
        tmp_path,
        {
            "Dockerfile": source,
            "app.py": "import worker\n\nif __name__ == '__main__':\n    worker.run()",
            "worker.py": "def run():\n    return True",
            "worker_old.py": "def run():\n    return True",
        },
    )

    report = Analyzer(tmp_path).report("stale")
    finding = next(item for item in report.findings if item.path == "worker_old.py")

    assert report.metrics["deployment_reference_uncertainty"]
    assert finding.risk == 1.0
    assert finding.recommendation == "review"
    assert any(
        risk.startswith("deployment_runtime_uncertainty:") for risk in finding.risks
    )
    assert "dynamic runtime module reference" in render_pretty(report, "all")


def test_unrelated_yaml_is_not_treated_as_deployment_metadata(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "docs/example.yaml": "handler: worker:app",
            "worker.py": "def run():\n    return True",
        },
    )

    report = Analyzer(tmp_path).report("scan")

    assert report.metrics["deployment_reference_files"] == []
    assert report.metrics["deployment_runtime_references"] == {}
