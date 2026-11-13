"""
Pytest configuration and shared fixtures.
"""
from pathlib import Path
import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "integration: marks integration-level tests")
    config.addinivalue_line("markers", "slow: marks slow performance tests")

