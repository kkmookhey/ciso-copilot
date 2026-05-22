"""azure_findings converts duck-typed Shasta Finding objects into the
platform's unified emission types."""
from dataclasses import dataclass, field

from azure_findings import convert_azure_findings, subscription_entity


class _Enum:
    """Stand-in for a Shasta StrEnum value (has .value)."""
    def __init__(self, value):
        self.value = value


@dataclass
class FakeFinding:
    check_id:          str
    title:             str
    description:       str
    severity:          object
    status:            object
    domain:            object
    resource_type:     str
    resource_id:       str
    region:            str = ""
    remediation:       str = ""
    soc2_controls:     list = field(default_factory=list)
    cis_aws_controls:  list = field(default_factory=list)
    cis_azure_controls: list = field(default_factory=list)
    cis_gcp_controls:  list = field(default_factory=list)
    mcsb_controls:     list = field(default_factory=list)
    iso27001_controls: list = field(default_factory=list)
    hipaa_controls:    list = field(default_factory=list)


_STORAGE_ID = ("/subscriptions/sub-1/resourceGroups/rg-a/providers/"
               "Microsoft.Storage/storageAccounts/mystorage")


def _finding(**kw):
    base = dict(
        check_id="azure-storage-001", title="Blob public access enabled",
        description="The storage account allows public blob access.",
        severity=_Enum("high"), status=_Enum("fail"), domain=_Enum("cloud"),
        resource_type="storageAccounts", resource_id=_STORAGE_ID,
        cis_azure_controls=["3.1"],
    )
    base.update(kw)
    return FakeFinding(**base)


def test_subscription_entity():
    e = subscription_entity("sub-1", "tenant-1")
    assert e.kind == "azure_subscription"
    assert e.natural_key == "sub-1"
    assert e.domain == "cloud"


def test_converts_a_failing_finding():
    out = convert_azure_findings([_finding()], "tenant-1", "sub-1")
    assert len(out["findings"]) == 1
    f = out["findings"][0]
    assert f.finding_type == "azure-storage-001"
    assert f.severity == "high"
    assert f.status == "fail"
    assert f.subject_entity_kind == "azure_storage_account"
    assert f.frameworks.get("cis_azure") == ["3.1"]


def test_emits_entity_and_edge_for_known_resource():
    out = convert_azure_findings([_finding()], "tenant-1", "sub-1")
    assert len(out["entities"]) == 1
    assert out["entities"][0].kind == "azure_storage_account"
    assert len(out["edges"]) == 1
    edge = out["edges"][0]
    assert edge.source_kind == "azure_subscription"
    assert edge.source_natural_key == "sub-1"
    assert edge.target_natural_key == _STORAGE_ID
    assert edge.kind == "contains"


def test_deduplicates_entities_across_findings():
    out = convert_azure_findings([_finding(), _finding()], "tenant-1", "sub-1")
    assert len(out["findings"]) == 2
    assert len(out["entities"]) == 1  # same resource, deduped


def test_unmapped_resource_keeps_finding_without_entity():
    f = _finding(resource_id="/subscriptions/sub-1/resourceGroups/rg/"
                 "providers/Microsoft.Cdn/profiles/p")
    out = convert_azure_findings([f], "tenant-1", "sub-1")
    assert len(out["findings"]) == 1
    assert out["findings"][0].subject_entity_kind is None
    assert out["entities"] == []


def test_skips_not_assessed_and_not_applicable():
    skipped = [_finding(status=_Enum("not_assessed")),
               _finding(status=_Enum("not_applicable"))]
    out = convert_azure_findings(skipped, "tenant-1", "sub-1")
    assert out["findings"] == []
