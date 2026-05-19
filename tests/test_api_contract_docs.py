from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]



def test_api_contract_docs_exist_and_are_linked():
    api_contract = REPO_ROOT / "docs" / "API_CONTRACT.md"
    deprecation_policy = REPO_ROOT / "docs" / "DEPRECATION_POLICY.md"
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme_en = (REPO_ROOT / "README_EN.md").read_text(encoding="utf-8")

    assert api_contract.exists()
    assert deprecation_policy.exists()
    assert "docs/API_CONTRACT.md" in readme
    assert "docs/DEPRECATION_POLICY.md" in readme
    assert "README_EN.md" in readme
    assert "README.md" in readme_en



def test_api_contract_marks_internal_paths():
    text = (REPO_ROOT / "docs" / "API_CONTRACT.md").read_text(encoding="utf-8")

    assert "wangchuan.v3.*" in text
    assert "wangchuan.memory_api" in text
    assert "wangchuan.recall_service" in text
    assert "wangchuan.runtime_state" in text
    assert "Stable" in text or "稳定" in text
    assert "WangchuanPipeline" in text



def test_deprecation_policy_mentions_changelog_and_minor_version_window():
    text = (REPO_ROOT / "docs" / "DEPRECATION_POLICY.md").read_text(encoding="utf-8")

    assert "CHANGELOG.md" in text
    assert "one minor version" in text or "一个 minor version" in text or "一个 minor 版本" in text
    assert "docs/API_CONTRACT.md" in text
