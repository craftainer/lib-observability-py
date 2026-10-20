"""Importing the renamed distribution warns that it moved to `craftainer-observability`."""

import importlib

import pytest

import craftainer_tool_observability


def test_import_emits_the_rename_deprecation_warning() -> None:
    """The final 0.1.x release points users at the new distribution and import name."""
    match = "craftainer-tool-observability is renamed to craftainer-observability"
    with pytest.warns(DeprecationWarning, match=match) as record:
        importlib.reload(craftainer_tool_observability)
    assert "import craftainer_observability" in str(record[0].message)
    assert "no further releases" in str(record[0].message)
