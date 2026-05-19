"""Tests for the GitHub App client."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest


# Test private key — never used for real signing. Generated once via:
#   openssl genrsa -traditional 2048
# Inlined to keep tests hermetic. Throwaway — not used for any real GitHub App.
TEST_PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAqpfSfP4Fyq1meTnEW3NxH2/JYwA0J6P4qulwVhxwRmcsE0Rh
7+O3TdxEzauaT+McFapRIUhmvWA+UxXN6sCIBZtMWk8625bc+jyY/xJyeoWlas/K
b3+J6HfMcglC2xJn0Of1Ez8mfVZE/XbNiANgMyKlEBPP+l5c2KA8e+U9QGT3ZKUa
SI0T0ezPr0y7kA6IQRz/rCL/kX+jSuSJi1I3I7v2KPeU7/fGyGkumuQr5SD2+mAt
8Ax+4hwVvSZaV6QJJj9SDpaQPIR6nKOrjRKui1JTyqQY0FlCiPF95bpAdMT72MXf
g1bsa0flI7Gdb2UO+gu6n7Q/7sdY4xueH7IpfwIDAQABAoIBABbiDKsVjANW5TMH
q640uwzjetkb0uMqXJXYgGgconbfKgtfld+O5Sy9ikEobqeeqErDsORNCPMIMPQG
Lbv6nYRbA4/tptCD2Rp7/G3itJZ4zOqZ+uaf7gjP4Q2+7kfinShppPcy9l/Drbdu
Mz22bjYNxKR2c+R6ueuY+uQHqQK1EBDFiILp44NLKHPtdoFM5FkJCXBmOvcPr9M7
cz/BLgZudYy7Or+ih3PSoh1eU56AC7raYJ87rHpZTAqwnrLx7KIMOMXSZmHbftv9
e4BHlpUZ1344rCbYyW09yUenjLTOmpleYniVJT8VpnLZzaAMX8K6OiVCjsO5jlQQ
asR8SkkCgYEA6QCHSI3lvlgkfdGzWoZILSGtW8OfWxjYrAIChzWWdNvk32SgdTbU
2Z607RyFnMJTTSpwlY1CiAUNneO+3tQARSqd0FZtfd2LcRdhWh+iBaWVmgGOAY5M
dF96Se0q0XNNui/UtyCvO+F23x8bAiEs+w3KI8qU4qeNVmn3B/bQehsCgYEAu25Z
erQW/9OSOI6nx/yZyExjIjibEvJbk2cwgSZIiOUi1RgggjacAflVCHI2k6eafdfL
ngvxQayE1RgU8W/C4PV2JrdIIzlwlfwPwBBeJJx1CtsMh0Uq7/DvqP6BgOAdFkVZ
h+5qmXBeNta3RCbRm5Bo71hgzD+7tjAlDrFtRG0CgYBgYxaTvheHSWE3J1OhpCEh
gmf7qQ44Giwv49j15AYsq3afrznto1Qj/lJsMDtZoM3jAyZ1x2z5ZdW/NiKUfXDr
K/kC4W4D/m0byIc+SA23dktP3UrIe/xGu+STxmfLI37JAdZmN0AmblvFa1G57M11
wbuYWMqEhLmkQMuvYLvXdwKBgQCsWE3C/HHhj0P26YXx6J3nhgXp468EfwIhylLZ
jsBH0Jp045iQ43IUhpXgDFWO9CCk8pbynvyabO4/m8M2NpQ1kr+v3fxhF2IlJ/+7
ldFbTNp6vu0IPVu8Agn9lPiz7mAQqHgo+9vdd2vKdSlTa3Z12xYCb3uilEAgyKhq
mE9nCQKBgC4o7vYJXDCrvn/cWqLKqG6Aux/qku8opvWODjAFmhlTKqSxZZ2J4WyR
871aJe9kd04nsEiDD1mPeS2kALM/In2E2TFngkU+/yBa0l6+h0edr+XogqN7DhEw
b3EoP+nV5RHATDKT6sjAnWyOjRKa8G0sSv1Tv9BFC0XX611CKV18
-----END RSA PRIVATE KEY-----
"""


@pytest.fixture(autouse=True)
def stub_secrets(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_SECRET_ARN", "arn:fake")
    import boto3
    class _FakeSm:
        def get_secret_value(self, SecretId):
            return {"SecretString": json.dumps({
                "app_id":        "123456",
                "client_id":     "Iv1.testclient",
                "client_secret": "test-secret",
                "private_key":   TEST_PRIVATE_KEY,
            })}
    monkeypatch.setattr(boto3, "client", lambda _name, **_kw: _FakeSm())
    import github_app as ga
    ga._credentials_cache = None
    ga._installation_token_cache.clear()
    yield


def test_mint_app_jwt_returns_valid_rs256_token():
    import jwt as pyjwt
    import github_app as ga
    token = ga.mint_app_jwt()
    # decode without verification to inspect claims
    decoded = pyjwt.decode(token, options={"verify_signature": False})
    assert decoded["iss"] == "Iv1.testclient"
    assert "iat" in decoded
    assert decoded["exp"] - decoded["iat"] == 600  # 10 minute TTL


def test_get_installation_token_caches_per_installation(monkeypatch):
    import github_app as ga
    calls: list[int] = []

    def fake_post(url, headers, body):
        calls.append(1)
        # return a far-future expiry so the cache short-circuits the second call
        return 201, {"token": "ghs_abc123", "expires_at": "2099-01-01T00:00:00Z"}

    monkeypatch.setattr(ga, "_http_post", fake_post)
    t1 = ga.get_installation_token(99999)
    t2 = ga.get_installation_token(99999)
    assert t1 == "ghs_abc123"
    assert t2 == "ghs_abc123"
    assert len(calls) == 1  # cached


def test_list_authorized_repos_returns_normalised_rows(monkeypatch):
    import github_app as ga
    monkeypatch.setattr(ga, "get_installation_token", lambda _id: "ghs_abc")

    def fake_get(url, headers):
        assert "page=1" in url and "per_page=30" in url
        return 200, {
            "total_count": 1,
            "repositories": [{
                "full_name":      "kk/foo",
                "default_branch": "main",
                "pushed_at":      "2026-05-18T10:00:00Z",
                "size":           1234,
                "language":       "Python",
                "private":        True,
            }],
        }, {}

    monkeypatch.setattr(ga, "_http_get", fake_get)
    out = ga.list_authorized_repos(installation_id=99999, page=1, per_page=30)
    assert out["repos"][0] == {
        "full_name":      "kk/foo",
        "default_branch": "main",
        "last_pushed_at": "2026-05-18T10:00:00Z",
        "size_kb":        1234,
        "primary_language": "Python",
        "is_private":     True,
    }
    assert out["next_page"] is None  # only one page


def test_list_authorized_repos_returns_next_page_marker(monkeypatch):
    import github_app as ga
    monkeypatch.setattr(ga, "get_installation_token", lambda _id: "ghs_abc")

    def fake_get(url, headers):
        return 200, {"total_count": 100, "repositories": [{"full_name": "kk/r",
            "default_branch": "main", "pushed_at": "2026-05-18T10:00:00Z",
            "size": 1, "language": None, "private": False}]}, {}

    monkeypatch.setattr(ga, "_http_get", fake_get)
    out = ga.list_authorized_repos(installation_id=99999, page=1, per_page=30)
    assert out["next_page"] == 2  # ceil(100 / 30) = 4 pages, so page 2 exists


def test_revoke_installation_returns_204(monkeypatch):
    import github_app as ga
    monkeypatch.setattr(ga, "get_installation_token", lambda _id: "ghs_abc")
    monkeypatch.setattr(ga, "_http_delete", lambda url, headers: (204, b""))
    ga.revoke_installation_token(99999)  # should not raise
