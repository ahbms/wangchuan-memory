from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"



def test_p1_docs_exist_and_readme_links_cover_them():
    expected = [
        "QUICKSTART.md",
        "CLI.md",
        "MCP.md",
        "STORAGE.md",
        "FAQ.md",
        "ARCHITECTURE.md",
        "TROUBLESHOOTING.md",
        "ALPHA_TRIAL_GUIDE.md",
        "FEEDBACK_TEMPLATE.md",
        "API_CONTRACT.md",
        "DEPRECATION_POLICY.md",
    ]
    for name in expected:
        assert (DOCS_DIR / name).exists(), f"missing docs/{name}"

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme_en = (REPO_ROOT / "README_EN.md").read_text(encoding="utf-8")
    assert (REPO_ROOT / "README_EN.md").exists()
    assert "English" in readme
    assert "README_EN.md" in readme
    assert "中文说明" in readme_en
    assert "中文" in readme_en
    for name in [
        "docs/QUICKSTART.md",
        "docs/CLI.md",
        "docs/STORAGE.md",
        "docs/FAQ.md",
        "docs/API_CONTRACT.md",
        "docs/DEPRECATION_POLICY.md",
        "docs/TROUBLESHOOTING.md",
        "docs/ALPHA_TRIAL_GUIDE.md",
        "docs/FEEDBACK_TEMPLATE.md",
    ]:
        assert name in readme
