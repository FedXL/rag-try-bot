import logging
import time
from dataclasses import dataclass, field

import redis.asyncio as redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@dataclass
class UserMessageLock:
    redis_url: str = "redis://redis:6379/2"
    lock_ttl_seconds: int = 660
    busy_notice_ttl_seconds: int = 20
    _redis: redis.Redis | None = field(default=None, init=False, repr=False)
    _redis_failed: bool = field(default=False, init=False, repr=False)
    _local_locks: dict[str, tuple[str, float]] = field(default_factory=dict, init=False, repr=False)
    _local_notices: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    def _lock_key(self, telegram_id: int) -> str:
        return f"telegram:inflight:{telegram_id}"

    def _notice_key(self, telegram_id: int) -> str:
        return f"telegram:busy_notice:{telegram_id}"

    async def _client(self) -> redis.Redis:
        if not self.redis_url:
            raise RedisError("redis_url is empty")
        if self._redis is None:
            self._redis = redis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    def _cleanup_local(self) -> None:
        now = time.monotonic()
        self._local_locks = {key: value for key, value in self._local_locks.items() if value[1] > now}
        self._local_notices = {key: expires_at for key, expires_at in self._local_notices.items() if expires_at > now}

    def _local_acquire(self, telegram_id: int, request_id: str) -> bool:
        self._cleanup_local()
        key = self._lock_key(telegram_id)
        if key in self._local_locks:
            return False
        self._local_locks[key] = (request_id, time.monotonic() + self.lock_ttl_seconds)
        return True

    def _local_should_send_busy_notice(self, telegram_id: int) -> bool:
        self._cleanup_local()
        key = self._notice_key(telegram_id)
        if key in self._local_notices:
            return False
        self._local_notices[key] = time.monotonic() + self.busy_notice_ttl_seconds
        return True

    def _local_release(self, telegram_id: int, request_id: str) -> None:
        self._cleanup_local()
        key = self._lock_key(telegram_id)
        current = self._local_locks.get(key)
        if current and current[0] == request_id:
            self._local_locks.pop(key, None)

    async def acquire(self, telegram_id: int, request_id: str) -> bool:
        if self._redis_failed:
            return self._local_acquire(telegram_id, request_id)
        try:
            client = await self._client()
            return bool(await client.set(self._lock_key(telegram_id), request_id, nx=True, ex=self.lock_ttl_seconds))
        except RedisError as exc:
            self._redis_failed = True
            logger.warning("stage=bot_lock event=redis_unavailable fallback=memory error=%s", exc)
            return self._local_acquire(telegram_id, request_id)

    async def should_send_busy_notice(self, telegram_id: int) -> bool:
        if self._redis_failed:
            return self._local_should_send_busy_notice(telegram_id)
        try:
            client = await self._client()
            return bool(await client.set(self._notice_key(telegram_id), "1", nx=True, ex=self.busy_notice_ttl_seconds))
        except RedisError as exc:
            self._redis_failed = True
            logger.warning("stage=bot_lock event=redis_unavailable fallback=memory error=%s", exc)
            return self._local_should_send_busy_notice(telegram_id)

    async def release(self, telegram_id: int, request_id: str) -> None:
        if self._redis_failed:
            self._local_release(telegram_id, request_id)
            return
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        end
        return 0
        """
        try:
            client = await self._client()
            await client.eval(script, 1, self._lock_key(telegram_id), request_id)
        except RedisError as exc:
            self._redis_failed = True
            logger.warning("stage=bot_lock event=release_failed fallback=memory error=%s", exc)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
