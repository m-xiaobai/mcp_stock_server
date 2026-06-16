from __future__ import annotations

import re
from typing import Any


class Redactor:
    _email_pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _phone_pattern = re.compile(r"\b1\d{10}\b")
    _token_pattern = re.compile(r"\b(?:sk|token|secret|api)[-_A-Za-z0-9]{6,}\b", re.IGNORECASE)

    def apply(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.apply(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.apply(item) for item in value]
        if isinstance(value, str):
            value = self._email_pattern.sub("[REDACTED_EMAIL]", value)
            value = self._phone_pattern.sub("[REDACTED_PHONE]", value)
            value = self._token_pattern.sub("[REDACTED_TOKEN]", value)
            return value
        return value
