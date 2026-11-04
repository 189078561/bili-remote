"""Candidate aggregation and reviewable changes, with manual-value protection."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import re
import secrets
import unicodedata

from .providers import BangumiProvider, TMDBProvider, PTGenProvider, ProviderError, title, field


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", str(text)).split()).casefold()


def deduplicate_titles(titles):
    # Keep a candidate's original display text; prefer explicitly classified
    # candidates over an untyped alias with the same normalized text.
    result, positions = [], {}
    for candidate in titles:
        if not candidate or not normalized(candidate.get("text", "")):
            continue
        key = normalized(candidate["text"])
        if key in positions:
            old = result[positions[key]]
            if old.get("language") == "und" and candidate.get("language") != "und":
                result[positions[key]] = deepcopy(candidate)
            continue
        positions[key] = len(result)
        result.append(deepcopy(candidate))
    return result


class MetadataResolver:
    def __init__(self, providers=None, *, capability_records=None):
        self.providers = providers if providers is not None else {"tmdb": TMDBProvider(), "bangumi": BangumiProvider(), "ptgen": PTGenProvider()}
        # Integration may load the redacted result of the explicit verification
        # command. GET capabilities itself performs no network access.
        for name, record in (capability_records or {}).items():
            if name in self.providers and isinstance(record, dict) and record.get("provider") == name and record.get("state") in {"verified", "unsupported", "unconfigured", "failed"}:
                allowed = {"provider", "state", "checked_at", "authentication", "supported_features", "sample_schema_hash", "warnings"}
                if set(record) <= allowed:
                    self.providers[name]._capabilities = deepcopy(record)

    def provider(self, name):
        if name not in self.providers:
            raise ProviderError("unknown_provider", "不支持的资料来源")
        return self.providers[name]

    async def capabilities(self, provider):
        return self.provider(provider).capabilities()

    async def search(self, query, media_type, providers, year=None, page=1):
        if not isinstance(query, str) or not query.strip() or len(query) > 200 or media_type not in {"movie", "series", "anime"}:
            raise ProviderError("invalid_query", "搜索名称或类型无效")
        if type(page) is not int or not 1 <= page <= 1000 or year is not None and (type(year) is not int or not 1000 <= year <= 9999):
            raise ProviderError("invalid_query", "搜索页码或年份无效")
        if not isinstance(providers, (list, tuple)) or not providers or len(providers) > 3:
            raise ProviderError("invalid_query", "资料来源列表无效")
        names = list(dict.fromkeys(providers))
        selected = [self.provider(name) for name in names]
        results = await asyncio.gather(*(provider.search(query.strip(), media_type, year=year, page=page) for provider in selected), return_exceptions=True)
        items, warnings, pages = [], [], []
        for name, result in zip(names, results):
            if isinstance(result, ProviderError):
                warnings.append({"code": result.code, "ref": name, "message": str(result)})
            elif isinstance(result, Exception):
                warnings.append({"code": "upstream_failed", "ref": name, "message": "资料来源暂不可用"})
            else:
                items.extend(result["items"])
                warnings.extend(result.get("warnings", []))
                pages.append(result.get("total_pages"))
        return {"items": items[:1000], "page": page, "total_pages": max(pages) if pages and all(type(x) is int for x in pages) else None, "warnings": warnings}

    async def resolve(self, selection, previous_context=None):
        if not isinstance(selection, dict) or selection.get("media_type") not in {"movie", "series", "anime"}:
            raise ProviderError("invalid_selection", "资料选择无效")
        provider = self.provider(selection.get("provider"))
        context = deepcopy(previous_context) if previous_context else {
            "schema_version": 1, "context_id": secrets.token_urlsafe(18), "revision": 0,
            "resources": [], "origins": [], "identity": None, "identity_candidates": [], "titles": [], "selected_title": field(None, "default", None),
            "metadata": {}, "episode": {}, "episode_mappings": [], "files": [], "effective_rules": {}, "warnings": [], "conflicts": []}
        changes = []
        try:
            proposal = await provider.detail(selection)
        except (ProviderError, KeyError, TypeError, AttributeError) as exc:
            if not isinstance(exc, ProviderError):
                exc = ProviderError("invalid_schema", "资料响应结构不受支持，请手动补充")
            context.setdefault("warnings", []).append({"code": exc.code, "ref": provider.name, "message": str(exc)})
            return {"context": context, "changes": [], "proposal": {}, "capabilities": [provider.capabilities()]}
        old_identity, proposed_identity = context.get("identity"), proposal.get("identity")
        if proposed_identity and selection.get("selected_by") == "automatic":
            proposed_identity = {**proposed_identity, "selected_by": "automatic", "confirmed": False}
        # A search heuristic cannot replace a selected identity. A user making
        # a new explicit provider/id selection may do so.
        locked = old_identity and old_identity.get("selected_by") == "manual" and selection.get("selected_by") == "automatic"
        if proposed_identity:
            changes.append({"field": "identity", "previous": old_identity, "proposed": proposed_identity, "applied": not locked})
            if not locked:
                context["identity"] = proposed_identity
        if locked:
            context.setdefault("warnings", []).append({"code": "identity_locked", "message": "已保留人工选择的影视身份"})
            return {"context": context, "changes": changes, "proposal": proposal, "capabilities": [provider.capabilities()]}
        context.setdefault("warnings", []).extend(proposal.get("warnings", []))
        if old_identity and proposed_identity and (old_identity.get("tmdb_id"), old_identity.get("bangumi_id")) != (proposed_identity.get("tmdb_id"), proposed_identity.get("bangumi_id")):
            context["titles"] = [x for x in context.get("titles", []) if x.get("source") == "manual"]
            context["episode_mappings"] = []
            context.setdefault("warnings", []).append({"code": "identity_changed", "message": "影视身份已改变，请复核保留的人工名称和季集"})
        context["titles"] = deduplicate_titles(context.get("titles", []) + proposal.get("titles", []))
        metadata = context.setdefault("metadata", {})
        for key, proposed in proposal.get("metadata", {}).items():
            old = metadata.get(key)
            protected = isinstance(old, dict) and old.get("manual") or isinstance(old, list) and any(isinstance(x.get("name"), dict) and x["name"].get("manual") for x in old if isinstance(x, dict))
            # Bangumi voice actors take precedence over a TMDB refresh unless
            # the user has explicitly edited the credits.
            if key == "voice_cast" and provider.name != "bangumi" and isinstance(old, list) and any((x.get("name") or {}).get("source") == "bangumi" for x in old):
                protected = True
            changes.append({"field": "metadata." + key, "previous": old, "proposed": proposed, "applied": not protected})
            if not protected:
                metadata[key] = proposed
        # There is deliberately no 'first English title wins' policy. An
        # explicit candidate or custom title is the user's choice.
        if selection.get("custom_title") is not None:
            custom = title(selection["custom_title"], "und", "custom", "manual", context["context_id"])
            if not custom:
                raise ProviderError("invalid_title", "自定义名称不能为空")
            context["titles"] = deduplicate_titles(context["titles"] + [custom])
            context["selected_title"] = {**field(custom["text"], "manual", custom["id"]), "manual": True, "confirmed": True}
        elif selection.get("title_id"):
            self.select_title(context, selection["title_id"])
        elif not (context.get("selected_title") or {}).get("value"):
            context.setdefault("warnings", []).append({"code": "title_selection_required", "message": "请选择名称候选或输入自定义名称"})
        season = proposal.get("season")
        if season is not None:
            episodes = proposal.get("episodes", [])
            ep = context.setdefault("episode", {})
            if not (ep.get("season") or {}).get("manual"):
                ep["season"] = {**field(season, provider.name, selection["id"]), "confirmed": True}
            if proposal.get("episodes_verified") and not (ep.get("season_episode_count") or {}).get("manual"):
                ep["season_episode_count"] = {**field(len(episodes), provider.name, selection["id"]), "confirmed": True}
        context["revision"] += 1
        return {"context": context, "changes": changes, "proposal": proposal, "capabilities": [provider.capabilities()]}

    @staticmethod
    def select_title(context, candidate_id):
        candidate = next((x for x in context.get("titles", []) if x["id"] == candidate_id), None)
        if not candidate:
            raise ProviderError("invalid_title", "名称候选不存在")
        for item in context["titles"]:
            item["selected"] = item["id"] == candidate_id
        context["selected_title"] = {**field(candidate["text"], "manual", candidate_id), "manual": True, "confirmed": True}
        return context
