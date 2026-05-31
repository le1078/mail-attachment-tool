"""
QA Testing conftest.py - Shared fixtures for email tool tests
"""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def mock_mail():
    """Create a mock IMAP mail object."""
    mail = MagicMock()
    mail.select.return_value = ("OK", [b"1"])
    mail.store.return_value = ("OK", [b"Success"])
    return mail


@pytest.fixture
def mock_log():
    """Create a mock log function that records messages."""
    messages = []

    def log_func(msg):
        messages.append(msg)

    log_func.messages = messages
    return log_func


@pytest.fixture
def sample_config():
    """Create a sample configuration dictionary."""
    return {
        "imap_server": "imap.example.com",
        "imap_port": 993,
        "email_user": "user@example.com",
        "email_pass": "password123",
        "send_user": "",
        "send_pass": "",
        "skip_ssl_verify": False,
    }
