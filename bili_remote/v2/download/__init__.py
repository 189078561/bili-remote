"""Download UI and recognition handoff adapters for Bili Remote V2."""

from .completion import DownloadCompletionAdapter, RecognitionProposal
from .status import project_download_job

__all__ = [
    "DownloadCompletionAdapter",
    "RecognitionProposal",
    "project_download_job",
]
