"""Project-free resource analysis and preview-first naming orchestration."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import PurePosixPath
from fractions import Fraction
import secrets
import threading
import time

from .errors import NamingError
from .filesystem import NamingFiles, AUDIO, SUBTITLES
from .execution import RenameExecutor


def _value(value):
    return value.get("value") if isinstance(value, dict) else value


def manual_field(value):
    return {"value": value, "source": "manual", "source_ref": None, "manual": True,
            "confirmed": True, "confidence": None, "updated_at": datetime.now(timezone.utc).isoformat()}


def episode_summary(files, scope, rules):
    from .episodes import field
    from .templates import effective_season

    result = deepcopy(files[0]["episode"])
    result["scope"] = scope
    numbers = {effective_season(item["episode"], rules) for item in files}
    if len(numbers) != 1 or None in numbers:
        result["season"] = field(None, "default")
        result["season_episode_count"] = field(None, "tmdb")
    return result


def manual_technical(key, item):
    item = _value(item)
    if item in (None, ""):
        return manual_field(None)
    if key == "frame_rate":
        try:
            rate = Fraction(item["numerator"], item["denominator"]) if isinstance(item, dict) else Fraction(str(item))
            if not 0 < rate <= 1000:
                raise ValueError
            item = {"numerator": rate.numerator, "denominator": rate.denominator}
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
            raise NamingError("invalid_frame_rate", "帧率须为有效正分数") from exc
    elif key in {"bit_depth", "width", "height"}:
        if type(item) is not int or not 1 <= item <= (64 if key == "bit_depth" else 100000):
            raise NamingError("invalid_media_value", "媒体参数数字无效", fields=[key])
    elif key == "hdr":
        if isinstance(item, str):
            item = [x for x in item.replace(",", ".").split(".") if x]
        if not isinstance(item, list) or len(item) > 10 or any(not isinstance(x, str) or len(x) > 40 for x in item):
            raise NamingError("invalid_media_value", "HDR 标签无效")
    elif not isinstance(item, (str, int)) or len(str(item)) > 100:
        raise NamingError("invalid_media_value", "媒体参数无效", fields=[key])
    return manual_field(item)


class NamingService:
    """One instance per authenticated session, or provide a session-scoped factory.

    ``tasks`` is the integration adapter into existing processing_tasks. Its
    enqueue_naming(batch_id, idempotency_key, undo=False) returns a TaskRef and
    enqueue_probe(resources, expected_fingerprints) returns a TaskRef. Neither
    receives context or generated text. Audit must be durable before enqueue.
    """
    def __init__(self, file_service, probe, *, owner_session, audit=None, tasks=None,
                 resolver=None, memory=None, origins=None, ttl_seconds=1800, max_entries=100):
        if not owner_session:
            raise ValueError("authenticated owner_session is required")
        self.files, self.probe = NamingFiles(file_service), probe
        self.owner = owner_session
        self.resolver, self.memory, self.origins = resolver, memory, origins
        self.tasks = tasks
        self.executor = RenameExecutor(self.files, audit) if audit else None
        self.ttl, self.max_entries = ttl_seconds, max_entries
        self.contexts, self.previews, self.requests = {}, {}, {}
        self.plan_signatures, self.episode_evidence = {}, {}
        self.complete_seasons = {}
        self.manual_rules, self.analyzed_episodes, self.latest_previews = {}, {}, {}
        self.lock = threading.RLock()

    def _remember_context(self, context):
        self.contexts[context["context_id"]] = deepcopy(context)
        while len(self.contexts) > self.max_entries:
            key = next(iter(self.contexts))
            self.contexts.pop(key)
            self.complete_seasons.pop(key, None)
            self.analyzed_episodes.pop(key, None)
            self.latest_previews.pop(key, None)

    async def analyze(self, resources, origins=None, context=None, rules=None, *, selection=None, manual_overrides=None):
        from .episodes import resolve_episode
        from .memory import validate_naming_rule
        validate_naming_rule(rules or {})
        if context is not None and not isinstance(context, dict) or manual_overrides is not None and not isinstance(manual_overrides, dict):
            raise NamingError("invalid_context", "上下文或人工覆盖必须为对象")
        if origins is not None and (not isinstance(origins, list) or len(origins) > 1000 or any(not isinstance(x, dict) for x in origins)):
            raise NamingError("invalid_origins", "下载来源列表无效")
        from ...file_service import _quick_fingerprint
        if isinstance(resources, list):
            canonical = []
            for ref in resources:
                path = self.files.path(ref)
                actual = self.files.reference(ref["root_key"], ref["relative_path"])
                if ref.get("fingerprint") and ref["fingerprint"] not in {actual["fingerprint"], _quick_fingerprint(path)}:
                    raise NamingError("stale_resource", "源文件已变化，请重新选择", status=409)
                canonical.append(actual)
            resources = canonical
        expanded = await asyncio.to_thread(self.files.expand, resources)
        prior = self._context(context) if context is not None else None
        result = deepcopy(prior) if prior else {
            "schema_version": 1, "context_id": secrets.token_urlsafe(18), "revision": 0,
            "identity": None, "identity_candidates": [], "titles": [], "selected_title": {**manual_field(None), "source": "default", "manual": False, "confirmed": False},
            "metadata": {}, "episode": {}, "episode_mappings": [], "warnings": [], "conflicts": [], "files": []}
        result["revision"] += 1
        result["resources"] = deepcopy(resources)
        # Client origins are locators only; facts are obtained from a trusted
        # download-origin repository, never promoted from caller assertions.
        verified_origins = []
        for origin in origins or []:
            if self.origins:
                found = self.origins.get(origin.get("job_id"), origin.get("original_resource"))
                if found:
                    reference = found.get("original_resource") or {}
                    actual = next((x for x in expanded if x["root_key"] == reference.get("root_key") and x["relative_path"] == reference.get("relative_path")), None)
                    if actual and reference.get("fingerprint") == actual["fingerprint"]:
                        verified_origins.append(deepcopy(found))
                    else:
                        result["warnings"].append({"code": "stale_origin", "message": "下载来源指纹与当前文件不符，未采用旧来源集数"})
            else:
                result["warnings"].append({"code": "unverified_origin", "message": "下载来源仓储未接入；未采用客户端集数事实"})
        result["origins"] = verified_origins
        proposal, changes, capabilities = {}, [], []
        if not selection and not result.get("identity") and self.resolver:
            from .recognition import search_hint
            from ..metadata.resolver import normalized
            query, year_hint = search_hint(expanded, verified_origins)
            if query:
                available = getattr(self.resolver, "providers", {})
                requests = []
                if "tmdb" in available:
                    requests.extend(self.resolver.search(query, kind, ["tmdb"], year=year_hint) for kind in ("movie", "series"))
                if "bangumi" in available:
                    requests.append(self.resolver.search(query, "anime", ["bangumi"], year=year_hint))
                pages = await asyncio.gather(*requests)
                result["identity_candidates"] = [item for page in pages for item in page["items"]]
                result["warnings"].extend(warning for page in pages for warning in page.get("warnings", []))
                exact = [item for item in result["identity_candidates"] if normalized(item.get("title", "")) == normalized(query) or normalized(item.get("original_title", "")) == normalized(query)]
                if len(exact) == 1:
                    item = exact[0]
                    selection = {"provider": item["provider"], "id": item["id"], "media_type": item["media_type"], "selected_by": "automatic"}
                if not selection:
                    result["warnings"].append({"code": "identity_selection_required", "message": "已返回搜索候选，请人工确认影视身份"})
        if selection and self.resolver:
            resolved = await self.resolver.resolve(selection, result)
            result = resolved["context"]
            changes = resolved.get("changes", [])
            capabilities = resolved.get("capabilities", [])
            proposal = resolved.get("proposal") or {}
            if proposal.get("episodes") is not None:
                self.episode_evidence[result["context_id"]] = deepcopy(proposal["episodes"])
            if proposal.get("identity"):
                self.complete_seasons[result["context_id"]] = (
                    proposal["identity"].get("tmdb_id"), proposal.get("complete_season_counts", {}))
        episode_override = (manual_overrides or {}).get("episode")
        if episode_override is not None:
            if not isinstance(episode_override, dict) or set(episode_override) - {"season", "episode", "scope"}:
                raise NamingError("invalid_episode", "人工季集字段无效")
            for key in ("season", "episode"):
                if key in episode_override:
                    number = episode_override[key]
                    if number is not None and (type(number) is not int or number < (0 if key == "season" else 1)):
                        raise NamingError("invalid_episode", "人工季集号无效")
            if episode_override.get("scope", "single") not in {"single", "season"}:
                raise NamingError("invalid_episode", "范围无效")
        for name, value in (manual_overrides or {}).items():
            if name == "episode":
                continue
            value = _value(value)
            if value is not None and (not isinstance(value, (str, int)) or isinstance(value, bool) or len(str(value)) > 500):
                raise NamingError("invalid_manual_value", "人工名称或年份无效", fields=[name])
            if name == "selected_title":
                result[name] = manual_field(_value(value))
            elif name in {"title_zh", "title_zh_hant", "title_ja", "original_title", "year"}:
                result["metadata"][name] = manual_field(_value(value))
            else:
                raise NamingError("unknown_field", "不支持的人工资料字段", fields=[name])
        from .memory import DEFAULT_RULES
        context_manual_rules = self.manual_rules.get(result["context_id"], {})
        remembered_sources = {}
        effective = deepcopy(DEFAULT_RULES)
        if self.memory:
            remembered = self.memory.get(result.get("identity"), _value((result.get("episode") or {}).get("season")))
            effective = {**deepcopy(DEFAULT_RULES), **remembered.get("effective", {}).get("naming_rule", {})}
            remembered_sources = deepcopy(remembered.get("effective", {}).get("sources", {}))
            episode_rules = remembered.get("effective", {}).get("episode_rules", {})
            effective["episode_rules"] = episode_rules
            remembered_title = remembered.get("effective", {}).get("selected_title")
            if remembered_title and not result["selected_title"].get("manual"):
                result["selected_title"] = {**manual_field(remembered_title), "source": "memory", "manual": False}
            for mapping in episode_rules.get("mappings", []):
                if mapping not in result["episode_mappings"]:
                    result["episode_mappings"].append(mapping)
        # Explicit request rules outrank rules retained by this work context;
        # remembered series/season rules remain below both.
        effective.update(deepcopy(context_manual_rules))
        effective.update(deepcopy(rules or {}))
        for name in ("source", "group"):
            if isinstance(effective.get(name), str):
                explicit = name in context_manual_rules or name in (rules or {})
                source = "manual" if explicit else "memory"
                effective[name] = {**manual_field(effective[name]), "source": source, "manual": source == "manual"}
                if explicit:
                    remembered_sources[f"naming_rule.{name}"] = {
                        "scope": "manual",
                        "reason": "explicit_rule",
                        "source_ref": None,
                    }
        result["effective_rules"] = {
            "naming_rule": {k: v for k, v in effective.items() if k != "episode_rules"},
            "episode_rules": effective.get("episode_rules", {}),
            "subtitle_rule": remembered.get("effective", {}).get("subtitle_rule", {}) if self.memory else {},
            "sources": remembered_sources,
        }
        old_files = {(x["ref"]["root_key"], x["ref"]["relative_path"]): x for x in result.get("files", [])}
        previous_episodes = self.analyzed_episodes.get(result["context_id"], {})
        result["files"] = []
        semaphore = asyncio.Semaphore(4)
        origins_by_path = {(x["original_resource"]["root_key"], x["original_resource"]["relative_path"]): x for x in verified_origins}
        downloaded_probes = self.origins.probes_for(verified_origins) if verified_origins and hasattr(self.origins, "probes_for") else {}

        async def probe_one(ref):
            async with semaphore:
                key = (ref["root_key"], ref["relative_path"])
                old = old_files.get(key)
                same = old and old["ref"].get("fingerprint") == ref["fingerprint"]
                path = self.files.path(ref)
                measured = downloaded_probes.get(key)
                if measured is None:
                    measured = await self.probe.probe(path, source_ref=ref, previous=old.get("probe") if same else None)
                elif same:
                    measured.update({name: deepcopy(val) for name, val in (old.get("probe") or {}).items()
                                     if name in measured and isinstance(val, dict) and val.get("manual")})
                if self.files.fingerprint(ref) != ref["fingerprint"]:
                    raise NamingError("stale_resource", "探测过程中源文件已变化", status=409)
                return measured
        probes = await asyncio.gather(*(probe_one(ref) for ref in expanded))
        for index, ref in enumerate(expanded):
            probe = probes[index]
            origin = origins_by_path.get((ref["root_key"], ref["relative_path"]))
            episode = resolve_episode(ref["relative_path"], origin=origin, identity=result.get("identity"),
                mappings=result.get("episode_mappings", []), tmdb_episodes=self.episode_evidence.get(result["context_id"], []),
                previous=previous_episodes.get((ref["root_key"], ref["relative_path"])), rules=effective, index=index)
            if episode_override:
                if "season" in episode_override and episode_override["season"] != _value(episode["episode"]["season"]):
                    episode["episode"]["season_episode_count"]["value"] = None
                for key in ("season", "episode"):
                    if key in episode_override:
                        episode["episode"][key] = manual_field(episode_override[key])
                episode["episode"]["scope"] = episode_override.get("scope", "single")
            result["files"].append({"ref": ref, "probe": probe, "episode": episode["episode"], "sidecars": self.files.sidecars(ref)})
            if episode.get("mapping") and episode["mapping"] not in result["episode_mappings"]:
                result["episode_mappings"].append(episode["mapping"])
            result["warnings"].extend(episode.get("warnings", []))
        if not result["files"]:
            raise NamingError("no_video", "所选资源中没有支持的视频")
        if prior and self.contexts.get(prior["context_id"], {}).get("revision") != prior["revision"]:
            raise NamingError("stale_context", "识别期间上下文已改变，请重试", status=409)
        scope = (episode_override or {}).get("scope")
        if not scope:
            scope = "season" if len(result["files"]) > 1 or any(ref.get("kind") == "directory" for ref in resources) else "single"
        self._apply_complete_seasons(result, effective)
        result["episode"] = episode_summary(result["files"], scope, effective)
        self.analyzed_episodes[result["context_id"]] = {
            (item["ref"]["root_key"], item["ref"]["relative_path"]): deepcopy(item["episode"])
            for item in result["files"]
        }
        naming_rule = result["effective_rules"]["naming_rule"]
        template_id = naming_rule.get("template_id")
        if not template_id:
            identity = result.get("identity") or {}
            media_type = identity.get("media_type")
            filename_file = next((item for item in result["files"] if (
                item.get("episode", {}).get("episode", {}).get("source") in {"filename", "bili", "tmdb"}
                or item.get("episode", {}).get("season", {}).get("source") == "filename"
            )), None)
            if identity.get("confirmed") and media_type in {"movie", "series", "anime"}:
                template_id = media_type
                rule_source = {"scope": "default", "reason": "confirmed_identity",
                               "source_ref": identity.get("tmdb_id") or identity.get("bangumi_id")}
            elif filename_file:
                template_id = "series"
                rule_source = {"scope": "default", "reason": "filename_episode",
                               "source_ref": filename_file["ref"]["relative_path"]}
            elif any(ref.get("kind") == "directory" for ref in resources):
                template_id = "season"
                rule_source = {"scope": "default", "reason": "directory_scope",
                               "source_ref": resources[0].get("relative_path")}
            elif len(result["files"]) > 1:
                template_id = "series"
                rule_source = {"scope": "default", "reason": "multi_file_scope",
                               "source_ref": None}
            else:
                template_id = "movie"
                rule_source = {"scope": "default", "reason": "movie_fallback",
                               "source_ref": result["files"][0]["ref"]["relative_path"]}
            naming_rule["template_id"] = template_id
            result["effective_rules"]["sources"]["naming_rule.template_id"] = rule_source
        elif "naming_rule.template_id" not in result["effective_rules"]["sources"]:
            scope = "manual" if "template_id" in (rules or {}) or "template_id" in context_manual_rules else "memory"
            result["effective_rules"]["sources"]["naming_rule.template_id"] = {
                "scope": scope, "reason": "explicit_rule" if scope == "manual" else "remembered_rule",
                "source_ref": None,
            }
        media_type = (result.get("identity") or {}).get("media_type")
        default_template = "movie" if media_type == "movie" else "anime" if media_type == "anime" else "tv_season" if result["episode"].get("scope") == "season" else "tv_episode"
        result["effective_rules"]["subtitle_rule"].setdefault("template_id", default_template)
        self._remember_context(result)
        self.last_changes, self.last_capabilities = changes, capabilities
        return deepcopy(result)

    def _apply_complete_seasons(self, context, rules):
        from .episodes import field
        identity = context.get("identity") or {}
        ref, counts = self.complete_seasons.get(context["context_id"], (None, {}))
        for item in context["files"]:
            episode = item["episode"]
            current = episode.get("season_episode_count") or {}
            if current.get("manual"):
                continue
            season = _value(episode.get("season"))
            total = counts.get(season)
            if (total and identity.get("confirmed") and ref == identity.get("tmdb_id")
                    and rules.get("episode_numbering") != "continuous"):
                # Unlike a season's currently listed episodes, this count is final.
                season_ref = f"tv/{ref}/season/{season if season is not None else 1}"
                episode["season_episode_count"] = field(total, "tmdb", season_ref, True)
            elif str(current.get("source_ref") or "").startswith("tv/"):
                episode["season_episode_count"] = field(None, "tmdb")

    def _context(self, context):
        if not isinstance(context, dict):
            raise NamingError("invalid_context", "缺少识别上下文")
        if context.get("schema_version") != 1 or type(context.get("revision")) is not int or not isinstance(context.get("context_id"), str):
            raise NamingError("invalid_context", "上下文版本或标识无效")
        trusted = self.contexts.get(context.get("context_id"))
        if not trusted or trusted["revision"] != context.get("revision"):
            raise NamingError("stale_context", "上下文已过期，请重新识别", status=409)
        # All changes must go through explicit overrides; echoed provenance is
        # not evidence. This also prevents fabricated probe values/sidecars.
        return deepcopy(trusted)

    def preview(self, context, overrides=None, *, rules=None):
        from .templates import effective_season, normalize_naming_text, render_filename
        from .episodes import resolve_episode
        with self.lock:
            current = self._context(context)
            overrides = deepcopy(overrides or {})
            if not isinstance(overrides, dict):
                raise NamingError("invalid_overrides", "人工覆盖必须为对象")
            if set(overrides) - {"file_values", "episode_overrides", "manual_names", "sidecars"}:
                raise NamingError("unknown_field", "未知命名覆盖字段")
            for name in ("file_values", "episode_overrides", "manual_names"):
                if name in overrides and (not isinstance(overrides[name], dict) or len(overrides[name]) > 1000):
                    raise NamingError("invalid_overrides", "覆盖列表无效", fields=[name])
            for name in (overrides.get("manual_names") or {}).values():
                if not isinstance(name, str) or not name.strip() or len(name) > 500:
                    raise NamingError("invalid_manual_value", "人工名称无效")
            if "sidecars" in overrides and (not isinstance(overrides["sidecars"], list) or len(overrides["sidecars"]) > 1000):
                raise NamingError("invalid_sidecar", "关联列表无效")
            incoming = deepcopy(rules or {})
            episode_rules = incoming.pop("episode_rules", None)
            chosen_rules = {**{**current.get("effective_rules", {}).get("naming_rule", {}), "episode_rules": current.get("effective_rules", {}).get("episode_rules", {})}, **incoming}
            from .memory import validate_naming_rule, validate_episode_rules
            validate_naming_rule(incoming)
            if episode_rules is not None:
                chosen_rules["episode_rules"] = {**chosen_rules.get("episode_rules", {}), **validate_episode_rules(episode_rules)}
            validate_naming_rule({k: v for k, v in chosen_rules.items() if k != "episode_rules"})
            paths = [x["ref"]["relative_path"] for x in current["files"]]
            years = [_value((overrides.get("file_values") or {}).get(path, {}).get("year")) for path in paths if isinstance((overrides.get("file_values") or {}).get(path, {}), dict)]
            if years and len(years) == len(paths) and all(x is not None and str(x) == str(years[0]) for x in years):
                current["metadata"]["year"] = manual_field(years[0])
            signature = json.dumps({"rules": chosen_rules, "overrides": overrides}, sort_keys=True)
            previous_signature = self.plan_signatures.get(current["context_id"])
            plan_changed = previous_signature != signature and (previous_signature is not None or chosen_rules != {**current.get("effective_rules", {}).get("naming_rule", {}), "episode_rules": current.get("effective_rules", {}).get("episode_rules", {})} or any(overrides.values()))
            if chosen_rules.get("template_id") == "season":
                result = self._season_preview(current, overrides, chosen_rules, signature, plan_changed)
                self.manual_rules.setdefault(current["context_id"], {}).update(deepcopy(rules or {}))
                return result
            operations, conflicts, manual_confirmations = [], [], []
            if current.get("identity") and not current["identity"].get("confirmed"):
                manual_confirmations.append({"code": "identity_confirmation_required", "message": "自动匹配的影视身份尚未确认，请人工选择或确认"})
            episodes = set()
            updated_files = []
            baseline = self.analyzed_episodes.get(current["context_id"], {})
            for index, original in enumerate(current["files"]):
                item = deepcopy(original)
                path = item["ref"]["relative_path"]
                origin = next((x for x in current.get("origins", []) if x.get("original_resource", {}).get("relative_path") == path and x.get("original_resource", {}).get("root_key") == item["ref"]["root_key"]), None)
                item["episode"] = resolve_episode(path, origin=origin, identity=current.get("identity"), mappings=current.get("episode_mappings", []),
                    tmdb_episodes=self.episode_evidence.get(current["context_id"], []), previous=baseline.get((item["ref"]["root_key"], path)), rules=chosen_rules, index=index)["episode"]
                fields = (overrides.get("file_values") or {}).get(path, {})
                if not isinstance(fields, dict):
                    raise NamingError("invalid_overrides", "逐文件覆盖必须为对象")
                permitted = {"title", "year", "source", "group", "season", "episode", "resolution", "video_codec", "audio_codec", "frame_rate", "bit_depth", "hdr", "audio_channels"}
                if set(fields) - permitted:
                    raise NamingError("unknown_field", "未知文件覆盖字段", fields=sorted(set(fields) - permitted))
                for key in {"title", "year", "source", "group"} & fields.keys():
                    value = _value(fields[key])
                    if value is not None and (not isinstance(value, (str, int)) or isinstance(value, bool) or key != "year" and not isinstance(value, str) or len(str(value)) > 500):
                        raise NamingError("invalid_manual_value", "人工命名字段无效", fields=[key])
                for key in set(fields) & set(item.get("probe", {})):
                    item["probe"][key] = manual_technical(key, fields[key])
                original_season = _value(item["episode"]["season"])
                for key in ("season", "episode"):
                    if key in fields:
                        item.setdefault("episode", {})[key] = manual_field(_value(fields[key]))
                episode_overrides = overrides.get("episode_overrides") or {}
                mapping_key = item.get("episode", {}).get("mapping_key")
                if path in episode_overrides or mapping_key in episode_overrides:
                    values = episode_overrides.get(path, episode_overrides.get(mapping_key))
                    if not isinstance(values, dict):
                        values = {"episode": values}
                    if set(values) - {"season", "episode"}:
                        raise NamingError("invalid_episode", "逐集季集字段无效")
                    for key, number in values.items():
                        item["episode"][key] = manual_field(number)
                if _value(item["episode"]["season"]) != original_season:
                    item["episode"]["season_episode_count"]["value"] = None
                manual_name = (overrides.get("manual_names") or {}).get(path)
                local_rules = deepcopy(chosen_rules)
                if manual_name is not None:
                    target_name = manual_name.strip()
                    if not target_name or any(ch in target_name for ch in ("/", "\\", "\0")):
                        raise NamingError("invalid_manual_value", "人工名称不能包含路径")
                    try:
                        from ...file_service import validate_filename
                        validate_filename(target_name)
                        target_name = normalize_naming_text(target_name, " ")
                        validate_filename(target_name)
                    except Exception as exc:
                        raise NamingError("invalid_manual_value", "人工名称无效") from exc
                    rendered = {"filename": target_name, "missing_fields": [], "warnings": [], "values": {}}
                else:
                    file_context = deepcopy(current)
                    if "year" in fields:
                        file_context["metadata"]["year"] = manual_field(_value(fields["year"]))
                    if "title" in fields:
                        file_context["selected_title"] = manual_field(_value(fields["title"]))
                    if "title" in fields:
                        local_rules.pop("custom_title", None)
                        local_rules.pop("selected_title_id", None)
                    for key in ("source", "group"):
                        if key in fields:
                            local_rules[key] = manual_field(_value(fields[key]))
                    rendered = render_filename(file_context, item, local_rules)
                for required in ("Title", "Episode"):
                    if required in rendered.get("missing_fields", []):
                        conflicts.append({"code": "missing_identity", "field": required, "ref": item["ref"], "message": "缺少名称或集数，请人工确认"})
                season = effective_season(item["episode"], local_rules)
                if origin and season is not None and (current.get("identity") or {}).get("tmdb_id") and not item["episode"]["episode"]["confirmed"]:
                    manual_confirmations.append({"code": "mapping_confirmation_required", "ref": item["ref"], "message": "B站与 TMDB 季集映射需人工确认"})
                media_type = (current.get("identity") or {}).get("media_type", local_rules.get("template_id", "movie"))
                if media_type in {"series", "anime", "anime_single"} or local_rules.get("template_id") in {"series", "anime"}:
                    if chosen_rules.get("episode_numbering") == "seasonal" and season is None:
                        conflicts.append({"code": "missing_season", "ref": item["ref"], "message": "季度集号模式需要可靠季号，请填写季号或改用连续集号"})
                    pair = (season, _value(item["episode"].get("episode")))
                    if pair in episodes:
                        conflicts.append({"code": "duplicate_episode", "ref": item["ref"], "message": "季集重复，请修正映射"})
                    episodes.add(pair)
                target = str(PurePosixPath(path).with_name(rendered["filename"]))
                operations.append({"source": item["ref"], "target_relative_path": target, "temporary_required": True,
                                   "sidecar_of": None, "source_fingerprint": item["ref"]["fingerprint"], "warnings": rendered.get("warnings", [])})
                item["sidecars"] = self._sidecars(item, overrides)
                updated_files.append(item)
                for sidecar in item["sidecars"]:
                    side_ref = sidecar["ref"]
                    old_path = PurePosixPath(side_ref["relative_path"])
                    suffix = "".join("." + x for x in sidecar.get("suffix_tokens", []))
                    name = PurePosixPath(target).stem + suffix + old_path.suffix
                    operations.append({"source": side_ref, "target_relative_path": str(old_path.with_name(name)),
                                       "temporary_required": True, "sidecar_of": item["ref"], "source_fingerprint": side_ref["fingerprint"], "warnings": []})
            changed_operations = [x for x in operations if x["source"]["relative_path"] != x["target_relative_path"]]
            conflicts.extend(self.files.validate_operations(changed_operations))
            current["files"] = updated_files
            self._apply_complete_seasons(current, chosen_rules)
            current["episode"] = episode_summary(updated_files, current["episode"]["scope"], chosen_rules)
            self._accept_plan(current, chosen_rules, signature, plan_changed)
            self.manual_rules.setdefault(current["context_id"], {}).update(deepcopy(rules or {}))
            return self._store_preview(operations, current, conflicts, manual_confirmations=manual_confirmations,
                                       rules=chosen_rules, overrides=overrides)

    def _accept_plan(self, current, rules, signature, changed):
        if changed:
            current["revision"] += 1
        current["effective_rules"]["naming_rule"] = {k: deepcopy(v) for k, v in rules.items() if k != "episode_rules"}
        current["effective_rules"]["episode_rules"] = deepcopy(rules.get("episode_rules", {}))
        for key in ("source", "group"):
            if isinstance(current["effective_rules"]["naming_rule"].get(key), str):
                current["effective_rules"]["naming_rule"][key] = manual_field(rules[key])
        selected = rules.get("custom_title")
        if rules.get("selected_title_id"):
            selected = next((x["text"] for x in current["titles"] if x["id"] == rules["selected_title_id"]), None)
        if selected is not None:
            current["selected_title"] = manual_field(selected)
        self.plan_signatures[current["context_id"]] = signature
        self._remember_context(current)

    def _season_preview(self, current, overrides, rules, signature, changed):
        from .templates import effective_season, render_filename, season_common_probe
        operations, conflicts, manual_confirmations = [], [], []
        if current.get("identity") and not current["identity"].get("confirmed"):
            manual_confirmations.append({
                "code": "identity_confirmation_required",
                "message": "自动匹配的影视身份尚未确认，请人工选择或确认",
            })
        directories = [ref for ref in current["resources"] if self.files.path(ref).is_dir()]
        if not directories:
            raise NamingError("directory_required", "整季目录模板需要选择目录")
        for original in directories:
            ref = self.files.reference(original["root_key"], original["relative_path"])
            members = [item for item in current["files"] if item["ref"]["root_key"] == ref["root_key"] and PurePosixPath(item["ref"]["relative_path"]).is_relative_to(ref["relative_path"])]
            common, warnings = season_common_probe(members)
            baseline = self.analyzed_episodes.get(current["context_id"], {})
            episodes = [{"episode": deepcopy(baseline.get((member["ref"]["root_key"], member["ref"]["relative_path"]), member["episode"]))}
                        for member in members]
            if rules.get("episode_numbering") == "seasonal" and rules.get("explicit_season") is not None:
                for member in episodes:
                    if not member["episode"]["season"].get("manual"):
                        member["episode"]["season"] = manual_field(rules["explicit_season"])
            item = {"ref": ref, "probe": common, "episode": episode_summary(episodes, "season", rules) if episodes else {}}
            rendered = render_filename(current, item, rules, template_id="season")
            if rules.get("episode_numbering") == "seasonal" and effective_season(item["episode"], rules) is None:
                conflicts.append({"code": "missing_season", "ref": ref, "message": "季度集号模式需要可靠季号"})
            if "Title" in rendered["missing_fields"]:
                conflicts.append({"code": "missing_identity", "field": "Title", "message": "缺少整季名称"})
            operations.append({"source": ref, "target_relative_path": str(PurePosixPath(ref["relative_path"]).with_name(rendered["filename"])),
                               "temporary_required": True, "sidecar_of": None, "source_fingerprint": ref["fingerprint"], "warnings": warnings + rendered["warnings"]})
        changed_operations = [x for x in operations if x["source"]["relative_path"] != x["target_relative_path"]]
        conflicts.extend(self.files.validate_operations(changed_operations))
        if len(directories) == 1 and item["episode"]:
            current["episode"] = item["episode"]
        self._accept_plan(current, rules, signature, changed)
        return self._store_preview(operations, current, conflicts, manual_confirmations=manual_confirmations,
                                   rules=rules, overrides=overrides)

    def _sidecars(self, item, overrides):
        sidecars = deepcopy(item.get("sidecars", []))
        for sidecar in (overrides.get("sidecars") or []):
            if not isinstance(sidecar, dict) or set(sidecar) - {"ref", "video_ref", "kind", "language", "suffix_tokens", "association", "confirmed"} or not isinstance(sidecar.get("video_ref"), dict):
                raise NamingError("invalid_sidecar", "关联文件对象无效")
            if sidecar.get("video_ref", {}).get("relative_path") != item["ref"]["relative_path"]:
                continue
            if not sidecar.get("confirmed") or sidecar.get("association") != "manual" or sidecar.get("kind") not in {"subtitle", "external_audio"}:
                raise NamingError("ambiguous_sidecar", "外音轨或人工关联必须明确确认")
            ref = sidecar["ref"]
            tokens = sidecar.get("suffix_tokens", [])
            if not isinstance(tokens, list) or len(tokens) > 10 or any(not isinstance(x, str) or len(x) > 40 or not x or any(c in x for c in "/\\\0.") for x in tokens):
                raise NamingError("invalid_sidecar", "关联文件尾缀无效")
            if ref.get("root_key") != item["ref"]["root_key"] or PurePosixPath(ref["relative_path"]).parent != PurePosixPath(item["ref"]["relative_path"]).parent:
                raise NamingError("invalid_sidecar", "关联文件必须位于同一目录")
            actual = self.files.reference(ref["root_key"], ref["relative_path"])
            if ref.get("fingerprint") and ref["fingerprint"] != actual["fingerprint"]:
                raise NamingError("stale_resource", "关联文件已变化", status=409)
            if sidecar["kind"] == "external_audio" and PurePosixPath(ref["relative_path"]).suffix.lower() not in AUDIO:
                raise NamingError("invalid_sidecar", "外音轨扩展名无效")
            if sidecar["kind"] == "subtitle" and PurePosixPath(ref["relative_path"]).suffix.lower() not in SUBTITLES:
                raise NamingError("invalid_sidecar", "字幕扩展名无效")
            sidecars = [x for x in sidecars if x["ref"]["relative_path"] != actual["relative_path"]]
            sidecars.append({**sidecar, "ref": actual})
        return sidecars

    def _store_preview(self, operations, context, conflicts=None, *, manual_confirmations=None, notices=None, **plan):
        now = time.monotonic()
        self.previews = {k: v for k, v in self.previews.items() if v["deadline"] > now}
        token = secrets.token_urlsafe(32)
        conflicts = deepcopy(conflicts or [])
        manual_confirmations = deepcopy(manual_confirmations or [])
        notices = deepcopy(notices or [])
        changed_operations = [item for item in operations
                              if item["source"]["relative_path"] != item["target_relative_path"]]
        unchanged_count = len(operations) - len(changed_operations)
        if not changed_operations:
            notices.append({"code": "no_changes", "message": "名称已符合规则，无需执行"})
        public = {
            "preview_token": token,
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=self.ttl)).isoformat(),
            "context_revision": context["revision"],
            "operations": deepcopy(operations),
            "conflicts": conflicts,
            "manual_confirmations": manual_confirmations,
            "notices": notices,
            "changed_count": len(changed_operations),
            "unchanged_count": unchanged_count,
            "conflict_count": len(conflicts),
            "manual_confirmation_count": len(manual_confirmations),
            "can_apply": bool(changed_operations) and not conflicts and not manual_confirmations,
        }
        if "episode" in context:
            public["episode"] = deepcopy(context["episode"])
            public["selected_file_episodes"] = [
                {"ref": item["ref"], "episode": item["episode"]} for item in context.get("files", [])
            ]
            public["file_episodes"] = {
                item["ref"]["relative_path"]: {key: _value(item["episode"].get(key)) for key in ("season", "episode")}
                for item in context.get("files", [])
            }
        self.latest_previews[context["context_id"]] = token
        self.previews[token] = {
            "public": public,
            "operations": deepcopy(changed_operations),
            "deadline": now + self.ttl,
            "context_id": context["context_id"],
            **deepcopy(plan),
        }
        while len(self.previews) > self.max_entries:
            self.previews.pop(next(iter(self.previews)))
        return deepcopy(public)

    async def apply(self, token, expected_revision, idempotency_key, *, undo=False):
        with self.lock:
            if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
                raise NamingError("invalid_idempotency_key", "请提供有效的幂等键")
            payload = (token, expected_revision, undo)
            if idempotency_key in self.requests:
                old, result = self.requests[idempotency_key]
                if old != payload:
                    raise NamingError("idempotency_conflict", "相同幂等键不能用于不同请求", status=409)
                return deepcopy(result)
            stored = self.previews.get(token)
            if not stored or stored["deadline"] < time.monotonic():
                raise NamingError("expired_preview", "预览已过期，请重新预览", status=409)
            preview = stored["public"]
            context = self.contexts.get(stored["context_id"])
            if expected_revision != preview["context_revision"] or not context or context["revision"] != expected_revision:
                raise NamingError("stale_context", "上下文已变化，请重新预览", status=409)
            if not stored.get("result") and self.latest_previews.get(stored["context_id"]) != token:
                raise NamingError("stale_preview", "已有更新的预览，请核对最新结果", status=409)
            if bool(stored.get("undo_of")) != undo:
                raise NamingError("invalid_preview", "预览类型不匹配", status=409)
            if not preview["can_apply"]:
                if preview.get("manual_confirmations"):
                    raise NamingError("manual_confirmation_required", "预览仍有文件需要人工确认", status=409,
                                      fields=preview["manual_confirmations"])
                if not preview.get("changed_count"):
                    raise NamingError("no_changes", "没有需要执行的重命名", status=409,
                                      fields=preview.get("notices", []))
                raise NamingError("rename_conflict", "预览存在冲突", status=409, fields=preview["conflicts"])
            if not self.executor or not self.tasks:
                raise NamingError("integration_unavailable", "持久化审计及公共执行器尚未接入", status=503)
            # A token can only create one batch, even under a different key.
            if stored.get("result"):
                result = stored["result"]
            else:
                if not stored.get("batch_id"):
                    stored["batch_id"] = self.executor.prepare(stored["operations"], owner=self.owner, undo_of=stored.get("undo_of"))
                    stored["queue_key"] = "v2-naming:" + secrets.token_urlsafe(24)
                batch_id = stored["batch_id"]
                task = self.tasks.enqueue_naming(batch_id, stored["queue_key"], undo=undo)
                result = {"task": task, "batch_id": batch_id}
                stored["result"] = deepcopy(result)
            self.requests[idempotency_key] = (payload, deepcopy(result))
            return deepcopy(result)

    async def undo_preview(self, batch_id):
        if not self.executor:
            raise NamingError("integration_unavailable", "持久化审计尚未接入", status=503)
        with self.lock:
            operations = self.executor.undo_operations(batch_id, owner=self.owner)
            conflicts = self.files.validate_operations(operations)
            context = {"context_id": "undo:" + batch_id, "revision": 1}
            self._remember_context(context)
            return self._store_preview(operations, context, conflicts, undo_of=batch_id)

    async def enqueue_probe(self, resources, expected_fingerprints):
        if not isinstance(expected_fingerprints, dict) or len(expected_fingerprints) > 1000:
            raise NamingError("invalid_fingerprints", "预期指纹列表无效")
        refs = self.files.expand(resources)
        for ref in refs:
            expected = expected_fingerprints.get(ref["root_key"] + "/" + ref["relative_path"], expected_fingerprints.get(ref["relative_path"]))
            if expected and expected != ref["fingerprint"]:
                raise NamingError("stale_resource", "探测前源文件已变化", status=409)
        if not self.tasks:
            raise NamingError("integration_unavailable", "公共执行器尚未接入", status=503)
        return self.tasks.enqueue_probe(refs, expected_fingerprints)

    async def probe_resources(self, resources, expected_fingerprints, *, should_cancel=lambda: False):
        """Processing handler result contains measurements only, never context."""
        refs = self.files.expand(resources)
        files = []
        for ref in refs:
            if should_cancel():
                raise NamingError("cancelled", "探测已取消", status=409)
            expected = expected_fingerprints.get(ref["root_key"] + "/" + ref["relative_path"], expected_fingerprints.get(ref["relative_path"]))
            if expected and expected != ref["fingerprint"]:
                raise NamingError("stale_resource", "源文件已变化，请重新探测", status=409)
            probe = await self.probe.probe(self.files.path(ref), source_ref=ref)
            if self.files.fingerprint(ref) != ref["fingerprint"]:
                raise NamingError("stale_resource", "探测过程中源文件已变化", status=409)
            files.append({"ref": ref, "probe": probe})
        return {"files": files, "artifact_refs": []}
