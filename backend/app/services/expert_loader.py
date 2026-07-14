"""Load PAW experts one at a time with a small LRU cache, and meter memory.

Rationale (per audit): each `paw.function(...)` constructs its own
`llama_cpp.Llama` instance and does NOT share the base model in RAM/Metal with
other loaded functions. So we must not assume a single ~594 MB runtime for N
experts. Phase 1 keeps at most `capacity` experts resident (default 1) and
records peak RSS so we can measure real memory before designing multi-expert.
"""

from __future__ import annotations

import gc
import os
import resource
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

import programasweights as paw


def _peak_rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports kilobytes.
    if sys.platform == "darwin":
        return int(rss)
    return int(rss) * 1024


@dataclass
class LoadMetric:
    program_id: str
    load_seconds: float
    peak_rss_bytes_after: int
    peak_rss_delta_bytes: int


@dataclass
class ExpertLoader:
    capacity: int = int(os.environ.get("PREPARE_OFFLINE_EXPERT_CAPACITY", "1"))
    _cache: "OrderedDict[str, Callable]" = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    metrics: list[LoadMetric] = field(default_factory=list)

    def get(self, program_id: str, *, offline: bool = True) -> Callable:
        with self._lock:
            if program_id in self._cache:
                self._cache.move_to_end(program_id)
                return self._cache[program_id]

            import time

            before = _peak_rss_bytes()
            t0 = time.perf_counter()
            fn = paw.function(program_id, offline=offline)
            load_s = time.perf_counter() - t0
            after = _peak_rss_bytes()

            self.metrics.append(
                LoadMetric(
                    program_id=program_id,
                    load_seconds=load_s,
                    peak_rss_bytes_after=after,
                    peak_rss_delta_bytes=max(0, after - before),
                )
            )

            self._cache[program_id] = fn
            self._cache.move_to_end(program_id)
            self._evict_if_needed()
            return fn

    def _evict_if_needed(self) -> None:
        while len(self._cache) > self.capacity:
            _, victim = self._cache.popitem(last=False)
            self._free(victim)

    @staticmethod
    def _free(fn: Callable) -> None:
        for attr in ("close", "free", "__del__"):
            closer = getattr(fn, attr, None)
            if callable(closer) and attr != "__del__":
                try:
                    closer()
                except Exception:
                    pass
        del fn
        gc.collect()

    def clear(self) -> None:
        with self._lock:
            while self._cache:
                _, victim = self._cache.popitem(last=False)
                self._free(victim)

    def metrics_summary(self) -> dict:
        return {
            "capacity": self.capacity,
            "resident": list(self._cache.keys()),
            "loads": [
                {
                    "program_id": m.program_id,
                    "load_seconds": round(m.load_seconds, 3),
                    "peak_rss_after_mb": round(m.peak_rss_bytes_after / 1e6, 1),
                    "peak_rss_delta_mb": round(m.peak_rss_delta_bytes / 1e6, 1),
                }
                for m in self.metrics
            ],
        }


_loader: ExpertLoader | None = None


def get_loader() -> ExpertLoader:
    global _loader
    if _loader is None:
        _loader = ExpertLoader()
    return _loader
