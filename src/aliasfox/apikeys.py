"""Per-user scoped API keys. Only an HMAC of the key is stored, never the key."""
import hashlib
import hmac
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import sessionmaker

from aliasfox.crypto import derive_key
from aliasfox.db import ApiKeyRow, User

SCOPES: tuple[str, ...] = ("read", "create", "edit", "deactivate", "delete")
PRESETS: dict[str, tuple[str, ...]] = {
    "bitwarden": ("create",),
    "readonly": ("read",),
    "assistant": ("read", "create", "edit", "deactivate"),
    "full": SCOPES,
}
MAX_KEYS_PER_USER = 20
_PREFIX = "afk_"


class TooManyKeys(Exception):
    pass


@dataclass(frozen=True)
class KeyInfo:
    id: int
    user_id: int
    name: str
    preset: str | None
    scopes: frozenset[str]
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; everything we store is UTC.
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _info(row: ApiKeyRow) -> KeyInfo:
    return KeyInfo(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        preset=row.preset,
        scopes=frozenset(s for s in row.scopes.split(",") if s in SCOPES),
        prefix=row.prefix,
        created_at=_aware(row.created_at),
        last_used_at=_aware(row.last_used_at),
        expires_at=_aware(row.expires_at),
    )


class ApiKeyStore:
    def __init__(self, engine: Engine, secret: str) -> None:
        self._hmac_key = derive_key(secret, info=b"aliasfox-apikey-v1")
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def _hash(self, key: str) -> bytes:
        return hmac.new(self._hmac_key, key.encode(), hashlib.sha256).digest()

    def create(
        self,
        user_id: int,
        name: str,
        scopes: Iterable[str],
        preset: str | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[str, KeyInfo]:
        scope_set = set(scopes)
        name = name.strip()
        if not name or not scope_set or not scope_set <= set(SCOPES):
            raise ValueError("a name and at least one known scope are required")
        key = _PREFIX + secrets.token_urlsafe(32)
        with self._sessions.begin() as s:
            # shortcut: count-then-insert can overshoot the cap under concurrent creates; single worker, fine.
            count = s.scalar(select(func.count()).select_from(ApiKeyRow).where(ApiKeyRow.user_id == user_id))
            if count >= MAX_KEYS_PER_USER:
                raise TooManyKeys()
            row = ApiKeyRow(
                user_id=user_id,
                name=name[:80],
                preset=preset,
                scopes=",".join(x for x in SCOPES if x in scope_set),
                key_hash=self._hash(key),
                prefix=key[len(_PREFIX) : len(_PREFIX) + 8],
                expires_at=expires_at,
            )
            s.add(row)
            s.flush()
            s.refresh(row)
            return key, _info(row)

    def list_for_user(self, user_id: int) -> "list[KeyInfo]":
        with self._sessions() as s:
            rows = s.scalars(
                select(ApiKeyRow).where(ApiKeyRow.user_id == user_id).order_by(ApiKeyRow.id)
            )
            return [_info(r) for r in rows]

    def revoke(self, user_id: int, key_id: int) -> bool:
        with self._sessions.begin() as s:
            return bool(
                s.execute(
                    delete(ApiKeyRow).where(ApiKeyRow.id == key_id, ApiKeyRow.user_id == user_id)
                ).rowcount
            )

    def lookup(self, key: str) -> KeyInfo | None:
        with self._sessions() as s:
            row = s.scalar(select(ApiKeyRow).where(ApiKeyRow.key_hash == self._hash(key)))
            if row is None:
                return None
            info = _info(row)
        if info.expires_at is not None and info.expires_at <= datetime.now(timezone.utc):
            return None
        return info

    def touch(self, key_id: int) -> None:
        with self._sessions.begin() as s:
            s.execute(
                update(ApiKeyRow)
                .where(ApiKeyRow.id == key_id)
                .values(last_used_at=datetime.now(timezone.utc))
            )

    def delete_for_user(self, user_id: int) -> int:
        with self._sessions.begin() as s:
            return s.execute(delete(ApiKeyRow).where(ApiKeyRow.user_id == user_id)).rowcount

    def delete_for_subjects(self, subjects: frozenset[str]) -> int:
        if not subjects:
            return 0
        with self._sessions.begin() as s:
            ids = select(User.id).where(User.auth_subject.in_(subjects))
            return s.execute(delete(ApiKeyRow).where(ApiKeyRow.user_id.in_(ids))).rowcount
