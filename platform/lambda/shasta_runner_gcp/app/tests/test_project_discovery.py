from project_discovery import discover_projects


def test_empty_input_returns_empty():
    assert discover_projects([], lambda p: "active") == {}


def test_classifies_each_project_by_probe_result():
    states = {"proj-a": "active", "proj-b": "empty"}
    out = discover_projects(["proj-a", "proj-b"], lambda p: states[p])
    assert out == {"proj-a": "active", "proj-b": "empty"}


def test_probe_exception_classifies_unknown():
    def probe(p):
        raise RuntimeError("permission denied")
    assert discover_projects(["proj-a"], probe) == {"proj-a": "unknown"}


def test_unexpected_probe_value_classifies_unknown():
    assert discover_projects(["proj-a"], lambda p: "weird") == {"proj-a": "unknown"}


def test_no_project_is_silently_dropped():
    out = discover_projects(["a", "b", "c"], lambda p: "active")
    assert set(out) == {"a", "b", "c"}


# ---------------------------------------------------------------------
# enumerate_projects
# ---------------------------------------------------------------------

from project_discovery import enumerate_projects


class _FakeClient:
    def __init__(self, projects):
        self._projects = projects

    def list_projects(self):
        return self._projects


def test_enumerate_returns_project_id_to_display_name():
    client = _FakeClient([
        {"project_id": "proj-a", "display_name": "Project A"},
        {"project_id": "proj-b", "display_name": "Project B"},
    ])
    assert enumerate_projects(client) == {
        "proj-a": "Project A",
        "proj-b": "Project B",
    }


def test_enumerate_falls_back_to_id_when_display_name_missing():
    client = _FakeClient([
        {"project_id": "proj-a", "display_name": ""},
        {"project_id": "proj-b"},
    ])
    assert enumerate_projects(client) == {"proj-a": "proj-a", "proj-b": "proj-b"}


def test_enumerate_skips_rows_without_project_id():
    client = _FakeClient([
        {"project_id": "proj-a", "display_name": "A"},
        {"project_id": "",       "display_name": "junk"},
        {"display_name": "no-id"},
    ])
    assert enumerate_projects(client) == {"proj-a": "A"}


def test_enumerate_empty_list_returns_empty_dict():
    client = _FakeClient([])
    assert enumerate_projects(client) == {}
