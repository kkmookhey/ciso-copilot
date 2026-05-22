from dataclasses import dataclass, field

from gcp_findings import convert_gcp_findings, project_entity


@dataclass
class _Enum:
    value: str


@dataclass
class _FakeFinding:
    """Duck-typed stand-in for a Shasta GCP Finding."""
    check_id:       str = "gcp-iam-1"
    title:          str = "Service account has owner role"
    description:    str = "An SA holds roles/owner."
    severity:       _Enum = field(default_factory=lambda: _Enum("high"))
    status:         _Enum = field(default_factory=lambda: _Enum("fail"))
    domain:         _Enum = field(default_factory=lambda: _Enum("iam"))
    resource_id:    str = ""
    resource_type:  str = "service_account"
    region:         str | None = None
    remediation:    str = "Remove the owner binding."
    soc2_controls:      list = field(default_factory=list)
    cis_aws_controls:   list = field(default_factory=list)
    cis_azure_controls: list = field(default_factory=list)
    cis_gcp_controls:   list = field(default_factory=lambda: ["1.4"])
    mcsb_controls:      list = field(default_factory=list)
    iso27001_controls:  list = field(default_factory=list)
    hipaa_controls:     list = field(default_factory=list)


def test_project_entity_shape():
    e = project_entity("my-proj", "tenant-1")
    assert e.kind == "gcp_project"
    assert e.natural_key == "my-proj"
    assert e.domain == "cloud"
    assert e.attributes["service"] == "gcp"


def test_convert_emits_a_finding_per_shasta_finding():
    out = convert_gcp_findings([_FakeFinding()], "tenant-1", "my-proj")
    assert len(out["findings"]) == 1
    f = out["findings"][0]
    assert f.finding_type == "gcp-iam-1"
    assert f.status == "fail"
    assert f.domain == "iam"
    assert f.frameworks  # cis_gcp at minimum


def test_convert_drops_not_assessed_findings():
    skipped = _FakeFinding(status=_Enum("not_assessed"))
    out = convert_gcp_findings([skipped], "tenant-1", "my-proj")
    assert out["findings"] == []


def test_convert_emits_subject_entity_and_edge_for_known_resource():
    rid = ("https://www.googleapis.com/compute/v1/projects/my-proj"
           "/zones/us-central1-a/instances/web-1")
    out = convert_gcp_findings(
        [_FakeFinding(resource_id=rid, resource_type="compute_instance")],
        "tenant-1", "my-proj")
    assert len(out["entities"]) == 1
    assert out["entities"][0].kind == "gcp_compute_instance"
    assert len(out["edges"]) == 1
    edge = out["edges"][0]
    assert edge.source_kind == "gcp_project"
    assert edge.source_natural_key == "my-proj"
    assert edge.target_kind == "gcp_compute_instance"
    assert edge.kind == "contains"


def test_convert_dedupes_repeated_resources():
    rid = ("https://www.googleapis.com/compute/v1/projects/my-proj"
           "/zones/us-central1-a/instances/web-1")
    out = convert_gcp_findings(
        [_FakeFinding(resource_id=rid), _FakeFinding(resource_id=rid)],
        "tenant-1", "my-proj")
    assert len(out["entities"]) == 1
    assert len(out["edges"]) == 1
    assert len(out["findings"]) == 2
