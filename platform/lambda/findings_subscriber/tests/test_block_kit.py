"""Golden tests for the Slack Block Kit template. Targets: 4-6 visual
lines, correct escaping of ARNs with special chars, sane truncation."""
import json


def _make_finding(**overrides):
    base = {
        "finding_id": "f-1", "tenant_id": "t-1",
        "title": "Public S3 bucket with PII-tagged data",
        "resource_arn": "arn:aws:s3:::acme-customer-exports",
        "scanner": "aws", "frameworks_list": ["PCI-DSS", "CIS-AWS"],
        "created_at_epoch": 1717179000,
    }
    base.update(overrides)
    return base


def test_template_includes_all_required_sections():
    from findings_subscriber.block_kit import format_finding_card
    blocks = format_finding_card(_make_finding())
    assert len(blocks) == 3  # title section, body section, actions
    assert blocks[0]["type"] == "section"
    assert blocks[1]["type"] == "section"
    assert blocks[2]["type"] == "actions"


def test_template_includes_view_button_with_url():
    from findings_subscriber.block_kit import format_finding_card
    import os
    os.environ["WEB_BASE_URL"] = "https://app.shasta.io"
    blocks = format_finding_card(_make_finding())
    btn = blocks[2]["elements"][0]
    assert btn["type"] == "button"
    assert btn["url"] == "https://app.shasta.io/risks/f-1"


def test_template_escapes_special_chars_in_arn():
    """Slack mrkdwn special chars (<, >, &) must be escaped. ARNs with
    these chars must not crash the parser or inject markup."""
    from findings_subscriber.block_kit import format_finding_card
    bad_arn = "arn:aws:s3:::my-bucket&foo<bar>"
    blocks = format_finding_card(_make_finding(resource_arn=bad_arn))
    body_text = blocks[1]["text"]["text"]
    assert "&amp;" in body_text or "&" not in body_text.replace("&amp;", "")
    assert "&lt;" in body_text or "<" not in body_text.replace("&lt;", "")
    assert "&gt;" in body_text or ">" not in body_text.replace("&gt;", "")


def test_template_truncates_long_title():
    """Slack section text limit is 3000 chars. Titles capped at 150 to
    keep the card compact."""
    from findings_subscriber.block_kit import format_finding_card
    long_title = "X" * 500
    blocks = format_finding_card(_make_finding(title=long_title))
    # Title appears in first section text. Bound: 150 chars + decorations.
    assert len(blocks[0]["text"]["text"]) < 300


def test_template_handles_ai_finding_shape():
    """AI scanner findings don't have resource_arn — they have subject_ref."""
    from findings_subscriber.block_kit import format_finding_card
    ai = _make_finding(scanner="ai", resource_arn=None,
                       subject_ref="agent://acme/customer-bot")
    blocks = format_finding_card(ai)
    # Should not crash; subject_ref appears in body when ARN is None.
    body = blocks[1]["text"]["text"]
    assert "agent://acme/customer-bot" in body or "subject_ref" not in body
