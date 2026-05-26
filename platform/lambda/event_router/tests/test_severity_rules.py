"""Severity rule table: action → severity floor for drift events."""
import severity_rules


def test_sg_open_to_world_is_high():
    assert severity_rules.drift_severity(
        action="AuthorizeSecurityGroupIngress",
        after={"ipPermissions": [{"fromPort": 22, "toPort": 22,
                                  "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}]},
    ) == "high"


def test_sg_open_to_world_cloudtrail_items_shape_is_high():
    """CloudTrail wraps lists as {'items': [...]}; the predicate must unwrap."""
    assert severity_rules.drift_severity(
        action="AuthorizeSecurityGroupIngress",
        after={"ipPermissions": {"items": [
            {"fromPort": 22, "toPort": 22,
             "ipRanges": {"items": [{"cidrIp": "0.0.0.0/0"}]}},
        ]}},
    ) == "high"


def test_sg_open_db_port_to_world_is_critical():
    assert severity_rules.drift_severity(
        action="AuthorizeSecurityGroupIngress",
        after={"ipPermissions": [{"fromPort": 3306, "toPort": 3306,
                                  "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}]},
    ) == "critical"


def test_mfa_deactivate_is_critical():
    assert severity_rules.drift_severity(action="DeactivateMFADevice", after={}) == "critical"


def test_root_console_login_is_critical():
    assert severity_rules.drift_severity(
        action="ConsoleLogin",
        after={"userIdentity": {"type": "Root"}},
    ) == "critical"


def test_iam_attach_admin_policy_is_high():
    assert severity_rules.drift_severity(
        action="AttachUserPolicy",
        after={"policyArn": "arn:aws:iam::aws:policy/AdministratorAccess"},
    ) == "high"


def test_bucket_public_acl_is_high():
    assert severity_rules.drift_severity(
        action="PutBucketAcl",
        after={"accessControlPolicy": {"grants": [{"grantee": {"uri": "http://acs.amazonaws.com/groups/global/AllUsers"}}]}},
    ) == "high"


def test_unknown_action_defaults_to_low():
    assert severity_rules.drift_severity(action="SomeBoringAction", after={}) == "low"


def test_action_in_rule_but_after_doesnt_match_pattern_defaults_to_medium():
    # SG ingress but bound to a private range: rule fires medium not high/critical
    assert severity_rules.drift_severity(
        action="AuthorizeSecurityGroupIngress",
        after={"ipPermissions": [{"fromPort": 22, "toPort": 22,
                                  "ipRanges": [{"cidrIp": "10.0.0.0/8"}]}]},
    ) == "medium"
