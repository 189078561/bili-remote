"""Subtitle-only adapter over the shared NamingMemoryRepository."""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping

from .rules import SubtitleRuleError, normalize_rule


class SubtitleRuleMemory:
    """Resolve and save formatting preferences without accepting generated text.

    The injected repository remains owned by the integration/naming layer. This
    adapter only passes a strict subtitle_rule object and derives identity keys
    from a confirmed shared Identity.
    """

    def __init__(self, repository):
        self.repository = repository

    async def resolve(self, identity=None, season=None, overrides=None):
        identity = _object_map(identity)
        confirmed_identity = _confirmed_identity(identity)
        confirmed_season = _positive_or_special_int(season)
        # Unknown identities never inherit another series' preference. The
        # repository can still return the global layer for a null identity.
        record = self.repository.get(
            confirmed_identity,
            confirmed_season if confirmed_identity is not None else None,
        )
        if inspect.isawaitable(record):
            record = await record
        layers, revision, base = _extract_layers(record)
        selected = {} if layers else dict(base)
        precedence = {"global": 0, "series": 1, "season": 2}
        layers.sort(key=lambda layer: precedence.get(_layer_scope(layer), -1))
        for layer in layers:
            layer_rule = _layer_rule(layer)
            if layer_rule:
                selected = _merge(selected, layer_rule)
        if overrides is not None:
            selected = _merge(selected, _normalize_patch(overrides))
        effective = normalize_rule(selected or {})
        return {
            "subtitle_rule": effective,
            "layers": [{"scope": _layer_scope(layer), "revision": _layer_revision(layer)} for layer in layers],
            "revision": revision,
            "identity_key": identity_key(confirmed_identity) if confirmed_identity else None,
            "season_key": confirmed_season if confirmed_identity else None,
        }

    async def save(self, *, scope, identity=None, season=None, rule=None, expected_revision):
        stored_rule = _normalize_patch(rule)
        if scope == "global":
            key = "global"
            season = None
        elif scope == "series":
            confirmed_identity = _confirmed_identity(identity)
            if confirmed_identity is None:
                raise SubtitleRuleError(
                    "series subtitle rules require a confirmed identity",
                    code="identity_unconfirmed", fields=["identity"],
                )
            key = identity_key(confirmed_identity)
            season = None
        elif scope == "season":
            confirmed_identity = _confirmed_identity(identity)
            season_value = _positive_or_special_int(season)
            if confirmed_identity is None or season_value is None:
                raise SubtitleRuleError(
                    "season subtitle rules require a confirmed identity and season",
                    code="identity_unconfirmed", fields=["identity", "season"],
                )
            key = f"{identity_key(confirmed_identity)}#season:{season_value}"
        else:
            raise SubtitleRuleError("scope must be global, series, or season", fields=["scope"])
        # No context or rendered text reaches the persistence boundary.
        result = self.repository.put(
            scope, key, {"subtitle_rule": stored_rule}, expected_revision,
        )
        if inspect.isawaitable(result):
            result = await result
        return result


def identity_key(identity):
    """Return the shared stable naming-memory key for a confirmed identity."""
    identity = _object_map(identity)
    if not _confirmed_identity(identity):
        raise SubtitleRuleError("identity is not confirmed", code="identity_unconfirmed", fields=["identity"])
    media_type = identity.get("media_type")
    tmdb_id = _external_id(identity.get("tmdb_id"))
    bangumi_id = _external_id(identity.get("bangumi_id"))
    if tmdb_id:
        namespace = "movie" if media_type == "movie" else "tv"
        return f"tmdb:{namespace}:{tmdb_id}"
    if bangumi_id:
        return f"bangumi:subject:{bangumi_id}"
    raise SubtitleRuleError("confirmed identity has no provider ID", code="identity_unconfirmed", fields=["identity"])


def _confirmed_identity(identity):
    identity = _object_map(identity)
    if not isinstance(identity, Mapping) or identity.get("confirmed") is not True:
        return None
    media_type = identity.get("media_type")
    if media_type not in {"movie", "series", "anime"}:
        return None
    if not (_external_id(identity.get("tmdb_id")) or _external_id(identity.get("bangumi_id"))):
        return None
    return dict(identity)


def _external_id(value):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return text if re.fullmatch(r"[0-9]{1,32}", text) else None


def _positive_or_special_int(value):
    if hasattr(value, "model_dump") or hasattr(value, "dict"):
        value = _object_map(value)
    if isinstance(value, Mapping) and "value" in value:
        value = value.get("value")
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _extract_layers(record):
    record = _object_map(record)
    if not isinstance(record, Mapping):
        return [], None, {}
    revision = record.get("revision")
    effective = _object_map(record.get("effective"))
    base = {}
    if isinstance(effective, Mapping):
        subtitle_rule = _object_map(effective.get("subtitle_rule"))
        if subtitle_rule:
            base = dict(subtitle_rule)
    elif isinstance(record.get("subtitle_rule"), Mapping):
        base = dict(record["subtitle_rule"])
    raw_layers = record.get("layers", [])
    layers = [_object_map(layer) for layer in raw_layers if _object_map(layer)] if isinstance(raw_layers, (list, tuple)) else []
    # EffectiveRules may expose only its flattened effective object. Retain a
    # source marker for observability without inventing a stored layer.
    if not layers and base:
        layers = [{"scope": record.get("scope", "effective"), "revision": revision, "subtitle_rule": base}]
    return layers, revision, base


def _layer_rule(layer):
    layer = _object_map(layer)
    rule = layer.get("subtitle_rule")
    rules = _object_map(layer.get("rules"))
    if rule is None:
        rule = rules.get("subtitle_rule")
    rule = _object_map(rule)
    return dict(rule) if rule else {}


def _layer_scope(layer):
    layer = _object_map(layer)
    return str(layer.get("scope") or "unknown")


def _layer_revision(layer):
    layer = _object_map(layer)
    return layer.get("revision")


def _merge(base, patch):
    result = dict(base)
    for key, value in patch.items():
        if key == "labels" and isinstance(result.get(key), Mapping) and isinstance(value, Mapping):
            labels = dict(result[key])
            labels.update(value)
            result[key] = labels
        else:
            result[key] = value
    return result


def _object_map(value):
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _normalize_patch(rule):
    if not isinstance(rule, Mapping):
        return normalize_rule(rule)
    normalized = normalize_rule(rule)
    # Validate with the same complete schema as rendering, but persist only
    # explicitly selected preferences so a series layer inherits global labels
    # and separators that the user did not change.
    patch = {key: normalized[key] for key in rule}
    if isinstance(rule.get("labels"), Mapping):
        patch["labels"] = {key: normalized["labels"][key] for key in rule["labels"]}
    return patch
