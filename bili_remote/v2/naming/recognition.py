"""Filename/source text is a search hint, never an authoritative identity."""
from pathlib import PurePosixPath
import re
import unicodedata

from ...workspace import parse_media_name


def search_hint(resources, origins):
    text = next((x.get("original_title") for x in origins if x.get("original_title")), None)
    if not text and resources:
        path = PurePosixPath(resources[0]["relative_path"])
        text = parse_media_name(path.name)["title"]
        if re.fullmatch(r"(?i)(?:EP?|第)?\s*\d{1,4}\s*(?:集|话|話)?", text):
            text = path.parent.name
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"(?i)(?:\bS\d{1,3}[ ._-]*(?:EP?\d{1,4})?|\bEP?\d{1,4}|第\s*\d+\s*[集话話]).*$", "", text)
    year_match = re.search(r"(?:^|[ ._([])((?:19|20)\d{2})(?:$|[ ._)\]])", text)
    year = int(year_match.group(1)) if year_match else None
    if year_match:
        text = text[:year_match.start()]
    text = re.sub(r"(?i)(?:^|[ ._])(\d{3,4}p|WEB[ ._-]*DL|WEBRIP|BLURAY|H[ .]?26[45]|AAC|HEVC|AVC).*$", "", text)
    text = re.sub(r"[._]+", " ", text)
    return " ".join(text.split()).strip(" -_")[:200], year
