from __future__ import annotations

import time
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, replace

from .bili import BiliAuthService
from .collage import CollageService
from .config import Settings
from .database import Database
from .downloader import DownloadManager, MaintenanceScheduler, YtDlpRunner
from .file_service import FileService
from .image_hosts import ImageHostService
from .metadata import MetadataService
from .naming import PreviewStore
from .processing import ProcessingTaskManager
from .publishing import PublishService
from .release_info import ReleaseInfoService
from .runtime_settings import RuntimeSettingsManager
from .screenshots import ScreenshotService
from .security import PasswordService, SecretBox, SessionService
from .tool_jobs import ToolJobs
from .torrents import TorrentService


class LoginRateLimiter:
    def __init__(self, *, attempts: int = 5, window_seconds: int = 300) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        failures = self._failures[key]
        while failures and now - failures[0] > self.window_seconds:
            failures.popleft()
        return len(failures) < self.attempts

    def fail(self, key: str) -> None:
        self._failures[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


@dataclass(slots=True)
class Services:
    settings: Settings
    database: Database
    secrets: SecretBox
    passwords: PasswordService
    sessions: SessionService
    bili: BiliAuthService
    runner: YtDlpRunner
    manager: DownloadManager
    maintenance: MaintenanceScheduler
    previews: PreviewStore
    processing: ProcessingTaskManager
    files: FileService
    collage: CollageService
    screenshots: ScreenshotService
    image_hosts: ImageHostService
    release_info: ReleaseInfoService
    torrents: TorrentService
    metadata: MetadataService
    publishing: PublishService
    runtime_settings: RuntimeSettingsManager
    login_limiter: LoginRateLimiter
    v2: object = None

    @classmethod
    def build(cls, settings: Settings) -> Services:
        # Keep deployment/environment defaults immutable for "restore defaults"
        # while services share a mutable runtime copy.
        runtime_copy = replace(settings)
        runtime_copy.validate()
        runtime_copy.ensure_directories()
        settings = runtime_copy
        database = Database(settings.database_path)
        secret_box = SecretBox(settings.app_secret)
        passwords = PasswordService(settings)
        sessions = SessionService(settings)
        bili = BiliAuthService(database, secret_box)
        runner = YtDlpRunner(settings, bili)
        manager = DownloadManager(settings, database, runner)
        maintenance = MaintenanceScheduler(settings, database)
        previews = PreviewStore()
        files = FileService(settings, database)
        collage = CollageService(settings, files)
        screenshots = ScreenshotService(settings, files)
        image_hosts = ImageHostService(files, database, secret_box)
        release_info = ReleaseInfoService(settings, files, database)
        torrents = TorrentService(files, database)
        publishing = PublishService(database, torrents)
        processing = ProcessingTaskManager(
            settings, database, previews, files, collage, screenshots
        )
        processing.tool_jobs = ToolJobs(database, release_info, torrents, image_hosts, screenshots)
        metadata = MetadataService(database, secret_box)
        runtime_settings = RuntimeSettingsManager(settings, database)
        result = cls(
            settings=settings,
            database=database,
            secrets=secret_box,
            passwords=passwords,
            sessions=sessions,
            bili=bili,
            runner=runner,
            manager=manager,
            maintenance=maintenance,
            previews=previews,
            processing=processing,
            files=files,
            collage=collage,
            screenshots=screenshots,
            image_hosts=image_hosts,
            release_info=release_info,
            torrents=torrents,
            publishing=publishing,
            metadata=metadata,
            runtime_settings=runtime_settings,
            login_limiter=LoginRateLimiter(),
        )

        from .v2.runtime import V2Runtime
        result.v2 = V2Runtime(result)
        processing.v2_runtime = result.v2
        manager.completion_hook = result.v2.download_completed
        return result

    async def startup(self) -> None:
        self.database.initialize()
        self.runtime_settings.load()
        self.v2.configure()
        await asyncio.to_thread(self.v2.recover)
        self.runner.refresh_aria2_executable()
        self.database.prepare_processing_tasks_on_startup()
        self.collage.cleanup_stale_temporary_directories()
        self.screenshots.cleanup_stale_temporary_directories()
        for stale in self.settings.runtime_dir.glob("cookies-*.txt"):
            try:
                stale.unlink()
            except OSError:
                pass
        self.manager.start()
        self.maintenance.start()
        self.processing.start()

    async def shutdown(self) -> None:
        await self.processing.stop()
        await self.maintenance.stop()
        await self.manager.stop()
        await self.bili.close()
