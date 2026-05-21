"""Tests for the check_id -> title catalog (scripts/check_titles.py)."""
import re
from pathlib import Path

import pytest

import check_titles

SHASTA_SRC = Path.home() / "Projects" / "Shasta" / "src"


def _shasta_check_ids() -> set[str]:
    """Static check_id literals declared anywhere in the Shasta source."""
    if not SHASTA_SRC.is_dir():
        pytest.skip(f"Shasta source not found at {SHASTA_SRC}")
    ids: set[str] = set()
    for py in SHASTA_SRC.rglob("*.py"):
        ids |= set(re.findall(r"""check_id=['"]([a-z0-9_-]+)['"]""",
                              py.read_text()))
    return ids


# --- _fallback_title --------------------------------------------------------

def test_fallback_strips_single_quoted_resource_name():
    assert check_titles._fallback_title(
        "User 'alice' has overly broad permissions"
    ) == "User has overly broad permissions"


def test_fallback_strips_multiple_quoted_names():
    assert check_titles._fallback_title(
        "PostgreSQL 'db1' in resource group 'rg-prod' allows public access"
    ) == "PostgreSQL in resource group allows public access"


def test_fallback_leaves_unquoted_title_unchanged():
    assert check_titles._fallback_title(
        "Root account has no MFA"
    ) == "Root account has no MFA"


def test_fallback_never_returns_empty():
    # A title that is nothing but a quoted token must not collapse to "".
    assert check_titles._fallback_title("'only-a-resource-name'") != ""


# --- resolve_check_title ----------------------------------------------------

def test_resolve_uses_catalog_for_known_check_id():
    # Pick any catalogued id and confirm the catalog value wins over raw_title.
    known_id = next(iter(check_titles.CHECK_TITLES))
    assert check_titles.resolve_check_title(
        known_id, "Raw 'resource' title"
    ) == check_titles.CHECK_TITLES[known_id]


def test_resolve_falls_back_for_unknown_check_id():
    assert check_titles.resolve_check_title(
        "totally-unknown-check-xyz", "User 'bob' lacks MFA"
    ) == "User lacks MFA"


# --- catalog completeness + quality -----------------------------------------

def test_catalog_covers_every_shasta_check_id():
    missing = _shasta_check_ids() - set(check_titles.CHECK_TITLES)
    assert not missing, (
        f"{len(missing)} Shasta check_id(s) absent from CHECK_TITLES: "
        f"{sorted(missing)}"
    )


def test_catalog_has_no_check_ids_unknown_to_shasta():
    extra = set(check_titles.CHECK_TITLES) - _shasta_check_ids()
    assert not extra, f"catalog keys not present in Shasta: {sorted(extra)}"


def test_catalog_titles_are_non_empty():
    empty = [k for k, v in check_titles.CHECK_TITLES.items() if not v.strip()]
    assert not empty, f"empty titles for: {empty}"


def test_catalog_titles_are_sentence_cased():
    """House style: a curated title reads as a sentence — starts uppercase,
    is multi-word, and is not the slug echoed back."""
    bad = [
        k for k, v in check_titles.CHECK_TITLES.items()
        if not v[:1].isupper()
        or " " not in v
        or v.strip().lower().replace(" ", "-") == k
    ]
    assert not bad, f"titles failing house style: {bad}"


# --- sync to the findings Lambdas -------------------------------------------

_REPO = Path(__file__).resolve().parent.parent
_MASTER = _REPO / "scripts" / "check_titles.py"
_LAMBDA_COPIES = [
    _REPO / "platform" / "lambda" / "findings_list" / "check_titles.py",
    _REPO / "platform" / "lambda" / "findings_rollup" / "check_titles.py",
]


@pytest.mark.parametrize("copy_path", _LAMBDA_COPIES, ids=lambda p: p.parent.name)
def test_lambda_copy_is_byte_identical_to_master(copy_path):
    assert copy_path.exists(), (
        f"{copy_path} missing — run scripts/sync_check_titles.py"
    )
    assert copy_path.read_bytes() == _MASTER.read_bytes(), (
        f"{copy_path} has drifted — run scripts/sync_check_titles.py"
    )
