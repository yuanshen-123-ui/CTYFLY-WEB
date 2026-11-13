"""轻量进程内缓存。

没有 Redis 时的低成本方案：每个 Gunicorn worker 各自持有一份 TTL 缓存。
只缓存匿名公共读数据，不缓存用户私有响应。
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Awaitable, Callable

from cachetools import TTLCache


class MemoryCache:
    def __init__(self) -> None:
        self._stores: dict[str, TTLCache] = {}
        self._lock = RLock()

    def _store(self, namespace: str, ttl: int, maxsize: int) -> TTLCache:
        with self._lock:
            store = self._stores.get(namespace)
            if store is None or store.ttl != ttl or store.maxsize != maxsize:
                store = TTLCache(maxsize=maxsize, ttl=ttl)
                self._stores[namespace] = store
            return store

    def get_or_set(
        self,
        namespace: str,
        key: str,
        ttl: int,
        loader: Callable[[], Any],
        maxsize: int = 256,
    ) -> Any:
        store = self._store(namespace, ttl, maxsize)
        try:
            return store[key]
        except KeyError:
            pass

        value = loader()
        with self._lock:
            store[key] = value
        return value

    async def get_or_set_async(
        self,
        namespace: str,
        key: str,
        ttl: int,
        loader: Callable[[], Awaitable[Any]],
        maxsize: int = 256,
    ) -> Any:
        store = self._store(namespace, ttl, maxsize)
        try:
            return store[key]
        except KeyError:
            pass

        value = await loader()
        with self._lock:
            store[key] = value
        return value

    def invalidate(self, namespace: str, prefix: str = "") -> None:
        with self._lock:
            store = self._stores.get(namespace)
            if not store:
                return
            for key in list(store.keys()):
                if not prefix or str(key).startswith(prefix):
                    store.pop(key, None)


cache = MemoryCache()
