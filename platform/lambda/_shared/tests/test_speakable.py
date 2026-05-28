# platform/lambda/_shared/tests/test_speakable.py
import pytest
from _shared.speakable import speakable_entity, speakable_payload


class TestSpeakableEntity:
    def test_aws_lambda_uses_display_name(self):
        e = {"kind": "aws_lambda", "display_name": "prod-ai-router",
             "natural_key": "arn:aws:lambda:us-east-1:111:function:prod-ai-router"}
        assert speakable_entity(e) == "the prod-ai-router Lambda"

    def test_aws_s3_bucket(self):
        e = {"kind": "aws_s3_bucket", "display_name": "acme-prod-exports",
             "natural_key": "arn:aws:s3:::acme-prod-exports"}
        assert speakable_entity(e) == "the acme-prod-exports bucket"

    def test_aws_iam_role(self):
        e = {"kind": "aws_iam_role", "display_name": "DeployerProd",
             "natural_key": "arn:aws:iam::111:role/DeployerProd"}
        assert speakable_entity(e) == "the DeployerProd IAM role"

    def test_ai_framework_stands_alone(self):
        e = {"kind": "ai_framework", "display_name": "langchain",
             "natural_key": "langchain"}
        assert speakable_entity(e) == "langchain"

    def test_ai_agent(self):
        e = {"kind": "ai_agent", "display_name": "pricing-agent",
             "natural_key": "repo/services/pricing/agent.py"}
        assert speakable_entity(e) == "the pricing-agent agent"

    def test_entra_user(self):
        e = {"kind": "entra_user", "display_name": "Sarah Chen",
             "natural_key": "sarah.chen@acme.io"}
        assert speakable_entity(e) == "Sarah Chen"

    def test_github_repo(self):
        e = {"kind": "github_repo", "display_name": "paying-system",
             "natural_key": "acme-org/paying-system"}
        assert speakable_entity(e) == "your paying-system repo"

    def test_unknown_kind_falls_back(self):
        e = {"kind": "weird_kind", "display_name": "thing",
             "natural_key": "thing-id"}
        assert speakable_entity(e) == "the weird_kind thing"

    def test_missing_display_name_uses_short_id(self):
        e = {"kind": "aws_lambda", "natural_key": "arn:aws:lambda:us-east-1:111:function:my-fn-abc"}
        # Tail "my-fn-abc" is 9 chars; _short_id truncates to 8 -> "my-fn-ab".
        assert speakable_entity(e) == "the my-fn-ab Lambda"

    def test_missing_display_name_plain_key(self):
        e = {"kind": "aws_s3_bucket", "natural_key": "plainbucket"}
        assert speakable_entity(e) == "the plainbuc bucket"


class TestSpeakablePayload:
    def test_walks_dict_adding_speakable_field(self):
        payload = {
            "resource": {
                "kind": "aws_lambda",
                "display_name": "prod-ai-router",
                "natural_key": "arn:aws:lambda:us-east-1:111:function:prod-ai-router",
            }
        }
        out = speakable_payload(payload)
        assert out["resource"]["speakable"] == "the prod-ai-router Lambda"
        # Original fields preserved.
        assert out["resource"]["natural_key"].startswith("arn:")

    def test_handles_top_level_entity(self):
        e = {"kind": "ai_framework", "display_name": "langchain",
             "natural_key": "langchain"}
        out = speakable_payload(e)
        assert out["speakable"] == "langchain"

    def test_non_entity_dict_untouched(self):
        payload = {"foo": "bar", "count": 5}
        assert speakable_payload(payload) == {"foo": "bar", "count": 5}
