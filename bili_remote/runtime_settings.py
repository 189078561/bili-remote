from __future__ import annotations

from typing import Any

from .config import Settings
from .database import Database
from .settings_registry import (
    RUNTIME_DEFINITIONS,
    deserialize_setting_value,
    parse_setting_value,
    runtime_env_defaults,
    serialize_setting_value,
)


class RuntimeSettingsManager:
    """Apply persisted runtime settings without changing in-flight operations.

    The web layer rejects mutations that overlap an active resolve/download. This
    manager therefore only needs to provide atomic validation/apply helpers for
    idle periods and startup restoration.
    """

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.env_defaults = runtime_env_defaults(settings)

    def load(self) -> None:
        stored = self.database.get_settings()
        for key, definition in RUNTIME_DEFINITIONS.items():
            raw = stored.get(key)
            if raw is None:
                value = self.env_defaults[key]
            else:
                value = deserialize_setting_value(definition, raw, self.settings)
            self._apply_one(definition, value)

    def current_values(self) -> dict[str, Any]:
        return runtime_env_defaults(self.settings)

    def prepare(self, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        parsed: dict[str, Any] = {}
        stored: dict[str, str] = {}
        for key, raw in values.items():
            definition = RUNTIME_DEFINITIONS.get(key)
            if definition is None:
                continue
            value = parse_setting_value(definition, raw, self.settings)
            parsed[key] = value
            stored[key] = serialize_setting_value(definition, value)
        return parsed, stored

    def apply(self, parsed: dict[str, Any]) -> None:
        for key, value in parsed.items():
            definition = RUNTIME_DEFINITIONS.get(key)
            if definition is not None:
                self._apply_one(definition, value)

    def _apply_one(self, definition: Any, value: Any) -> None:
        attr = definition.runtime_attr or definition.key
        if definition.runtime_scale != 1:
            value = int(round(float(value) * definition.runtime_scale))
        setattr(self.settings, attr, value)
