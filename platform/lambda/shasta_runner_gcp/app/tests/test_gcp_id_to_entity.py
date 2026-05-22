from gcp_id_to_entity import parse_gcp_id


def test_parses_compute_instance_selflink():
    rid = ("https://www.googleapis.com/compute/v1/projects/my-proj"
           "/zones/us-central1-a/instances/web-1")
    parsed = parse_gcp_id(rid)
    assert parsed["kind"] == "gcp_compute_instance"
    assert parsed["natural_key"] == rid
    assert parsed["display_name"] == "web-1"
    assert parsed["attributes"]["project"] == "my-proj"


def test_parses_storage_bucket_full_resource_name():
    rid = "//storage.googleapis.com/projects/_/buckets/my-data-bucket"
    parsed = parse_gcp_id(rid)
    assert parsed["kind"] == "gcp_storage_bucket"
    assert parsed["display_name"] == "my-data-bucket"


def test_parses_vpc_network_selflink():
    rid = ("https://www.googleapis.com/compute/v1/projects/my-proj"
           "/global/networks/default")
    parsed = parse_gcp_id(rid)
    assert parsed["kind"] == "gcp_vpc_network"
    assert parsed["display_name"] == "default"


def test_parses_service_account_full_resource_name():
    rid = ("//iam.googleapis.com/projects/my-proj/serviceAccounts/"
           "svc@my-proj.iam.gserviceaccount.com")
    parsed = parse_gcp_id(rid)
    assert parsed["kind"] == "gcp_service_account"
    assert parsed["display_name"] == "svc@my-proj.iam.gserviceaccount.com"


def test_parses_cloud_run_service_full_resource_name():
    rid = ("//run.googleapis.com/projects/my-proj/locations/us-central1"
           "/services/api-gateway")
    parsed = parse_gcp_id(rid)
    assert parsed["kind"] == "gcp_cloud_run_service"
    assert parsed["display_name"] == "api-gateway"


def test_non_cloud_run_services_path_returns_none():
    # `services` under Service Usage is NOT a Cloud Run service.
    rid = "//serviceusage.googleapis.com/projects/my-proj/services/compute.googleapis.com"
    assert parse_gcp_id(rid) is None


def test_unknown_collection_returns_none():
    assert parse_gcp_id(
        "https://www.googleapis.com/compute/v1/projects/p/global/widgets/w"
    ) is None


def test_non_resource_string_returns_none():
    assert parse_gcp_id("just-a-name") is None
    assert parse_gcp_id("") is None
    assert parse_gcp_id(None) is None
