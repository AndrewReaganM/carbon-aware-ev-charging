"""Pytest configuration for Carbon-Aware EV Charging tests."""
import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow custom integrations to load during tests."""
    yield
