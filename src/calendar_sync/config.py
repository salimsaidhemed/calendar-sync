from dataclasses import dataclass
from collections.abc import Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    dry_run: bool = True
    nextcloud_url: str = "https://nextcloud.tcadc.com"

    @classmethod
    def from_env(cls, environment: Mapping[str, str]) -> "Settings":
        dry_run = environment.get("SYNC_DRY_RUN", "true").strip().lower()
        if dry_run not in {"true", "false"}:
            raise ValueError("SYNC_DRY_RUN must be true or false")
        url = environment.get("NEXTCLOUD_URL", cls.nextcloud_url)
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("NEXTCLOUD_URL must be HTTPS without credentials, query or fragment")
        return cls(dry_run=dry_run == "true", nextcloud_url=url)
