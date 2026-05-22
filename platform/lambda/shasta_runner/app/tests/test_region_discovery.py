# app/tests/test_region_discovery.py
"""Four-state region footprint probe: classify each enabled region
active / default_only / empty / unknown."""
import boto3
from botocore.stub import Stubber

from region_discovery import (RegionDiscovery, classify_region,
                              discover_regions, probe_region)


# ---- classify_region (pure) ----

def test_classify_active_when_real_resources():
    assert classify_region(has_real=True, has_default_vpc=True, errored=False) == "active"
    assert classify_region(has_real=True, has_default_vpc=False, errored=False) == "active"


def test_classify_default_only():
    assert classify_region(has_real=False, has_default_vpc=True, errored=False) == "default_only"


def test_classify_empty():
    assert classify_region(has_real=False, has_default_vpc=False, errored=False) == "empty"


def test_classify_unknown_on_error_regardless_of_signals():
    assert classify_region(has_real=True, has_default_vpc=True, errored=True) == "unknown"
    assert classify_region(has_real=False, has_default_vpc=False, errored=True) == "unknown"


# ---- probe_region ----

def _ec2_with(vpcs, instances):
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_response("describe_vpcs", {"Vpcs": vpcs})
    stub.add_response("describe_instances",
                      {"Reservations": [{"Instances": instances}] if instances else []})
    stub.activate()
    return ec2


def _empty_client(service, op, key):
    c = boto3.client(service, region_name="us-east-1",
                     aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(c)
    stub.add_response(op, {key: []})
    stub.activate()
    return c


def _make_client_factory(ec2):
    """Returns make_client(service) — ec2 is the stubbed one; the other
    services return empty so the test isolates the VPC/EC2 signal."""
    empties = {
        "lambda": lambda: _empty_client("lambda", "list_functions", "Functions"),
        "rds":    lambda: _empty_client("rds", "describe_db_instances", "DBInstances"),
        "elbv2":  lambda: _empty_client("elbv2", "describe_load_balancers", "LoadBalancers"),
        "ecs":    lambda: _empty_client("ecs", "list_clusters", "clusterArns"),
        "eks":    lambda: _empty_client("eks", "list_clusters", "clusters"),
    }
    def _make(service):
        if service == "ec2":
            return ec2
        return empties[service]()
    return _make


def test_probe_region_active_with_nondefault_vpc():
    ec2 = _ec2_with([{"VpcId": "vpc-1", "IsDefault": False}], [])
    state = probe_region(_make_client_factory(ec2), "us-east-1")
    assert state == "active"


def test_probe_region_default_only():
    ec2 = _ec2_with([{"VpcId": "vpc-d", "IsDefault": True}], [])
    state = probe_region(_make_client_factory(ec2), "us-east-1")
    assert state == "default_only"


def test_probe_region_empty_when_no_vpc_no_resources():
    ec2 = _ec2_with([], [])
    state = probe_region(_make_client_factory(ec2), "us-east-1")
    assert state == "empty"


def test_probe_region_unknown_on_error():
    def _boom(service):
        raise RuntimeError("AccessDenied")
    assert probe_region(_boom, "us-east-1") == "unknown"


# ---- discover_regions ----

def test_discover_regions_builds_state_map():
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_response("describe_regions",
                      {"Regions": [{"RegionName": "us-east-1"},
                                   {"RegionName": "eu-west-1"}]})
    stub.activate()

    def make_client_for_region(region):
        # both regions probe empty
        return _make_client_factory(_ec2_with([], []))

    rd = discover_regions(ec2, make_client_for_region)
    assert set(rd.region_states) == {"us-east-1", "eu-west-1"}
    assert rd.method == "footprint_probe"


def test_discover_regions_degrades_when_listing_fails():
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_client_error("describe_regions", "UnauthorizedOperation")
    stub.activate()

    rd = discover_regions(ec2, lambda r: None)
    assert rd.method == "degraded_default"
    # degraded fallback regions are scanned conservatively as 'unknown'
    assert all(s == "unknown" for s in rd.region_states.values())
    assert "us-east-1" in rd.region_states
