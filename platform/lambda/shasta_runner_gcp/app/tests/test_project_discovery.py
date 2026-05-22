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
