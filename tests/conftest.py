import pytest


@pytest.fixture(autouse=True)
def legacy_automatic_box_mode(monkeypatch):
    # Existing device/order tests exercise the optional automatic completion mode.
    # Box handoff tests explicitly enable the new operator confirmation workflow.
    monkeypatch.setenv('REQUIRE_BOX_CONFIRMATION', 'false')
