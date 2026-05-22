"""azure_id_to_entity parses an Azure Resource Manager ID into an entity
descriptor, or returns None for IDs we have no kind mapping for."""
from azure_id_to_entity import parse_azure_id

_STORAGE = ("/subscriptions/sub-1/resourceGroups/rg-a/providers/"
            "Microsoft.Storage/storageAccounts/mystorage")
_VM = ("/subscriptions/sub-1/resourceGroups/rg-a/providers/"
       "Microsoft.Compute/virtualMachines/myvm")
_UNMAPPED = ("/subscriptions/sub-1/resourceGroups/rg-a/providers/"
             "Microsoft.Cdn/profiles/myprofile")


def test_parses_storage_account():
    out = parse_azure_id(_STORAGE)
    assert out["kind"] == "azure_storage_account"
    assert out["natural_key"] == _STORAGE
    assert out["display_name"] == "mystorage"
    assert out["attributes"]["subscription"] == "sub-1"
    assert out["attributes"]["resource_group"] == "rg-a"
    assert out["attributes"]["service"] == "azure"


def test_parses_virtual_machine():
    assert parse_azure_id(_VM)["kind"] == "azure_virtual_machine"


def test_case_insensitive_provider_match():
    lower = _STORAGE.replace("Microsoft.Storage", "microsoft.storage")
    assert parse_azure_id(lower)["kind"] == "azure_storage_account"


def test_unmapped_type_returns_none():
    assert parse_azure_id(_UNMAPPED) is None


def test_non_arm_string_returns_none():
    assert parse_azure_id("not-an-azure-id") is None
    assert parse_azure_id("arn:aws:s3:::bucket") is None


def test_empty_returns_none():
    assert parse_azure_id("") is None
    assert parse_azure_id(None) is None
