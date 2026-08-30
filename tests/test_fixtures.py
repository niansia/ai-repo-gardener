from __future__ import annotations

import shutil
from pathlib import Path

from repo_gardener.analysis import Analyzer

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
REQUIRED_FIXTURES = {
    "agent_diff_orphan",
    "false_positive_plugin",
    "flat_four_domains",
    "monkeypatch_string_path",
    "partial_replacement",
    "rename_not_stale",
    "src_prefix_import",
    "stale_v2",
    "style_human_baseline",
}


def test_required_fixture_catalog_is_present() -> None:
    available = {path.name for path in FIXTURE_ROOT.iterdir() if path.is_dir()}

    assert REQUIRED_FIXTURES <= available


def test_src_prefix_fixture_resolves_when_copied(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE_ROOT / "src_prefix_import", tmp_path, dirs_exist_ok=True)

    analyzer = Analyzer(tmp_path)

    assert analyzer.graph.edges["main"] == {"service"}
    assert "service" in analyzer.graph.reachable


def test_partial_replacement_fixture_never_becomes_safe(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE_ROOT / "partial_replacement", tmp_path, dirs_exist_ok=True)

    report = Analyzer(tmp_path).report("stale")

    finding = next(item for item in report.findings if item.path == "parser_v2.py")
    assert finding.risk >= 0.55
    assert finding.recommendation == "review"
    assert any(
        item["type"] == "symbols_missing_from_replacement" for item in finding.evidence
    )


def test_monkeypatch_fixture_keeps_string_target_live(tmp_path: Path) -> None:
    shutil.copytree(
        FIXTURE_ROOT / "monkeypatch_string_path", tmp_path, dirs_exist_ok=True
    )

    analyzer = Analyzer(tmp_path)

    assert "target_old" in analyzer.graph.reachable
