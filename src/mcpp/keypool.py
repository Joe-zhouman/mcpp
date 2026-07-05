from __future__ import annotations


class KeyPool:
    """Round-robin key pool with health-based pause.

    Keys that fail (mark_bad) are skipped until resume() is called.
    When all keys are paused, next() raises RuntimeError.
    """

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("Pool requires at least one key")
        self._keys = list(keys)
        self._bad: set[str] = set()
        self._idx = 0

    def next(self) -> str:
        for _ in range(len(self._keys)):
            key = self._keys[self._idx]
            self._idx = (self._idx + 1) % len(self._keys)
            if key not in self._bad:
                return key
        raise RuntimeError("No healthy keys available in pool")

    def mark_bad(self, key: str) -> None:
        self._bad.add(key)

    def resume(self, key: str) -> None:
        self._bad.discard(key)

    @property
    def healthy_count(self) -> int:
        return len(self._keys) - len(self._bad)

    def statuses(self) -> list[dict]:
        """Return key statuses for admin display (keys are masked)."""
        return [
            {"index": i, "key": k[:4] + "...", "paused": k in self._bad}
            for i, k in enumerate(self._keys)
        ]

    @property
    def current(self) -> str:
        """Return the last-returned key (for marking bad after failure)."""
        idx = (self._idx - 1) % len(self._keys)
        return self._keys[idx]
