# app/tests/test_region_discovery.py
"""Region discovery — classify_regions turns per-region probe results
into the active/skipped/errored breakdown the scanner scopes itself to."""
from region_discovery import RegionDiscovery, classify_regions


def test_active_regions_are_those_with_resources_plus_us_east_1():
    enabled = ["us-east-1", "us-west-2", "eu-west-1"]
    probe = {"us-east-1": False, "us-west-2": True, "eu-west-1": False}
    rd = classify_regions(enabled, probe)
    # us-west-2 has resources; us-east-1 is always included (global anchor).
    assert rd.active_regions == ["us-east-1", "us-west-2"]
    assert rd.skipped_empty == ["eu-west-1"]
    assert rd.errored_regions == []
    assert rd.method == "tagging_api"


def test_errored_region_is_treated_as_active_never_skipped():
    enabled = ["us-east-1", "ap-south-1"]
    probe = {"us-east-1": True, "ap-south-1": None}  # None = sweep errored
    rd = classify_regions(enabled, probe)
    assert "ap-south-1" in rd.active_regions
    assert rd.errored_regions == ["ap-south-1"]
    assert rd.skipped_empty == []


def test_us_east_1_active_even_when_empty_and_never_in_skipped():
    rd = classify_regions(["us-east-1"], {"us-east-1": False})
    assert rd.active_regions == ["us-east-1"]
    assert rd.skipped_empty == []


def test_account_empty_everywhere_still_scans_us_east_1():
    enabled = ["us-east-1", "us-west-2"]
    rd = classify_regions(enabled, {"us-east-1": False, "us-west-2": False})
    assert rd.active_regions == ["us-east-1"]


import boto3
from botocore.stub import Stubber

from region_discovery import discover_regions


def _ec2_stub(region_names):
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_response(
        "describe_regions",
        {"Regions": [{"RegionName": r} for r in region_names]},
    )
    stub.activate()
    return ec2


def _tagging_stub(has_resources: bool):
    tag = boto3.client("resourcegroupstaggingapi", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(tag)
    mappings = [{"ResourceARN": "arn:aws:sqs:...:q"}] if has_resources else []
    stub.add_response("get_resources", {"ResourceTagMappingList": mappings})
    stub.activate()
    return tag


def test_discover_regions_splits_active_and_empty():
    ec2 = _ec2_stub(["us-east-1", "eu-west-1"])
    clients = {"us-east-1": _tagging_stub(True), "eu-west-1": _tagging_stub(False)}
    rd = discover_regions(ec2, lambda r: clients[r])
    assert rd.active_regions == ["us-east-1"]
    assert rd.skipped_empty == ["eu-west-1"]
    assert rd.method == "tagging_api"


def test_discover_regions_treats_sweep_error_as_active():
    ec2 = _ec2_stub(["us-east-1", "ap-south-1"])

    def tagging_for(region):
        if region == "ap-south-1":
            raise RuntimeError("AccessDenied")
        return _tagging_stub(True)

    rd = discover_regions(ec2, tagging_for)
    assert "ap-south-1" in rd.active_regions
    assert rd.errored_regions == ["ap-south-1"]


def test_discover_regions_degrades_when_region_listing_fails():
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_client_error("describe_regions", "UnauthorizedOperation")
    stub.activate()

    rd = discover_regions(ec2, lambda r: None)
    assert rd.method == "degraded_default"
    assert "us-east-1" in rd.active_regions
    assert len(rd.active_regions) > 1  # the documented default set
