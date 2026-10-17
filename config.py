import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _admins() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    out = set()
    for x in raw.split(","):
        x = x.strip()
        if not x:
            continue
        try:
            out.add(int(x))
        except ValueError:
            raise RuntimeError(f"Invalid ADMIN_IDS entry: {x!r}")
    return out


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    manager_bot_token: str
    admin_ids: set[int]
    zip_path: Path
    zip_member: str
    db_path: Path
    keys_db_path: Path
    bind_host: str
    bind_port: int
    max_results: int
    show_full_fields: bool
    allow_lookup_during_import: bool
    api_rate_limit_per_minute: int
    import_batch_size: int
    import_commit_every: int


def load_settings(require_bot: bool = False) -> Settings:
    token = os.getenv("MANAGER_BOT_TOKEN", "").strip()
    admins = _admins()
    if require_bot:
        if not token or token.startswith("PUT_"):
            raise RuntimeError("MANAGER_BOT_TOKEN missing in .env")
        if not admins:
            raise RuntimeError("ADMIN_IDS missing in .env")

    return Settings(
        base_dir=BASE_DIR,
        manager_bot_token=token,
        admin_ids=admins,
        zip_path=Path(os.getenv("ZIP_PATH", "/root/db2/extracted/users_data.zip")),
        zip_member=os.getenv("ZIP_MEMBER", "users_data.json"),
        db_path=Path(os.getenv("DB_PATH", "/root/db2/lookup/users_lookup.db")),
        keys_db_path=Path(os.getenv("KEYS_DB_PATH", "/root/db2/lookup/api_keys.db")),
        bind_host=os.getenv("BIND_HOST", "127.0.0.1"),
        bind_port=int(os.getenv("BIND_PORT", "8080")),
        max_results=max(1, min(int(os.getenv("MAX_RESULTS", "10")), 50)),
        show_full_fields=_bool("SHOW_FULL_FIELDS", True),
        allow_lookup_during_import=_bool("ALLOW_LOOKUP_DURING_IMPORT", False),
        api_rate_limit_per_minute=max(1, int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "120"))),
        import_batch_size=max(100, int(os.getenv("IMPORT_BATCH_SIZE", "10000"))),
        import_commit_every=max(1000, int(os.getenv("IMPORT_COMMIT_EVERY", "100000"))),
    )
