"""
server/email_adapter.py
=======================
Email delivery adapter interface and development simulators for Mini Swiggy.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import json
import os
import pathlib
from typing import Optional

LOGS_DIR = pathlib.Path(__file__).parent.parent / "logs"
DEV_EMAILS_FILE = LOGS_DIR / "dev_emails.jsonl"


class EmailAdapter(ABC):
    """Abstract interface for transactional email delivery."""

    @abstractmethod
    def send_verification_email(self, to_email: str, verify_url: str) -> bool:
        """Send an account verification link to the given email address."""
        pass


class DevelopmentEmailAdapter(EmailAdapter):
    """
    Development email adapter:
    1. Prints the verification link to the console log.
    2. Persists the email to logs/dev_emails.jsonl for programmatic test assertions.
    """

    def __init__(self, log_file: pathlib.Path = DEV_EMAILS_FILE) -> None:
        self.log_file = log_file

    def send_verification_email(self, to_email: str, verify_url: str) -> bool:
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # 1. Console log
        print(f"\n[EMAIL SIMULATOR] To: {to_email}")
        print(f"[EMAIL SIMULATOR] Click link to verify account:")
        print(f"[EMAIL SIMULATOR] {verify_url}\n")

        # 2. Persist to JSONL file
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "to": to_email,
                "verify_url": verify_url,
                "timestamp": timestamp,
            }
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            return True
        except Exception as exc:
            print(f"[EMAIL SIMULATOR] Failed to write email log: {exc}")
            return False

    def get_latest_email(self, to_email: Optional[str] = None) -> Optional[dict]:
        """Utility for test suites to retrieve the most recent sent email."""
        if not self.log_file.exists():
            return None
        matches = []
        with open(self.log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if to_email is None or data.get("to") == to_email:
                        matches.append(data)
                except Exception:
                    continue
        return matches[-1] if matches else None


# Default singleton instance for the server
default_email_adapter: EmailAdapter = DevelopmentEmailAdapter()
