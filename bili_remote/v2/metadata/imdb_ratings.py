"""Daily public IMDb title ratings, keyed only by the IMDb title identifier."""
import gzip
import io
from threading import Lock
from time import monotonic
import zlib

import httpx


class IMDbRatings:
    url = "https://datasets.imdbws.com/title.ratings.tsv.gz"

    def __init__(self):
        self.lock = Lock()
        self.table = b""
        self.refresh_at = 0

    def download(self):
        with httpx.stream("GET", self.url, timeout=8) as response:
            response.raise_for_status()
            archive = io.BytesIO()
            for chunk in response.iter_bytes():
                archive.write(chunk)
                if archive.tell() > 24 * 1024 * 1024:
                    raise ValueError("IMDb 评分文件过大")
        archive.seek(0)
        limit = 64 * 1024 * 1024
        with gzip.GzipFile(fileobj=archive) as compressed:
            table = compressed.read(limit + 1)
        if len(table) > limit or not table.startswith(b"tconst\taverageRating\tnumVotes\n") or not table.endswith(b"\n"):
            raise ValueError("IMDb 评分文件格式有变")
        return table

    def lookup(self, title_id):
        with self.lock:
            if monotonic() >= self.refresh_at:
                try:
                    self.table = self.download()
                except (httpx.HTTPError, OSError, EOFError, ValueError, zlib.error):
                    self.refresh_at = monotonic() + 300
                else:
                    self.refresh_at = monotonic() + 86400
            table = self.table
        if not table:
            raise ValueError("IMDb 评分资料暂时无法读取。")
        key = b"\n" + title_id.encode("ascii") + b"\t"
        start = table.find(key)
        if start >= 0:
            start += len(key)
            score, votes = table[start:table.index(b"\n", start)].decode("ascii").split("\t")
            return {"average": score, "votes": votes}
        return None


official_ratings = IMDbRatings()
