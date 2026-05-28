# platform/lambda/tools/tests/test_create_pr_with_bump.py
from unittest.mock import patch
from tools.create_pr_with_bump import handle, _bump_version_in_requirements


def test_bump_replaces_pinned_version():
    content = "fastapi==0.95.0\nlangchain==0.0.184\npydantic>=2.0\n"
    out = _bump_version_in_requirements(content, "langchain", "0.0.354")
    assert "langchain==0.0.354" in out
    assert "langchain==0.0.184" not in out
    assert "fastapi==0.95.0" in out


def test_bump_no_match_returns_original():
    content = "fastapi==0.95.0\n"
    out = _bump_version_in_requirements(content, "langchain", "0.0.354")
    assert out == content


@patch("tools.create_pr_with_bump._mcp_client")
def test_create_pr_orchestration(mock_client):
    mock_client.call.side_effect = [
        # 1. get_file_contents -> current requirements.txt
        {"content": "langchain==0.0.184\n", "sha": "blob-sha"},
        # 2. create_branch
        {"ref": "refs/heads/shasta/bump-langchain-0.0.354"},
        # 3. create_or_update_file -> commit
        {"commit": {"sha": "commit-sha"}},
        # 4. create_pull_request
        {"number": 42, "html_url": "https://github.com/acme/paying-system/pull/42"},
    ]
    result = handle({
        "repo":             "acme/paying-system",
        "dependency":       "langchain",
        "target_version":   "0.0.354",
        "reviewer_lookup":  "priya",
        "manifest_path":    "requirements.txt",
    }, {"sub": "x"})
    assert result["pr_number"] == 42
    assert "url" in result
    assert "speakable" in result
    assert "PR" in result["speakable"]
