"""Isolated PAW inference child used to release llama.cpp memory per program."""

from __future__ import annotations

import resource
import sys
import time


def _rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return (value if sys.platform == "darwin" else value * 1024) / 1_000_000


def run_child(connection, program_id: str, text: str, max_tokens: int) -> None:
    started = time.perf_counter()
    function = None
    try:
        import programasweights as paw

        function = paw.function(program_id, offline=True)
        output = function(text, max_tokens=max_tokens, temperature=0.0)
        connection.send(
            {
                "ok": True,
                "output": str(output).strip(),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "peak_rss_mb": round(_rss_mb(), 1),
            }
        )
    except Exception as exc:
        connection.send(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "peak_rss_mb": round(_rss_mb(), 1),
            }
        )
    finally:
        if function is not None:
            closer = getattr(function, "close", None) or getattr(function, "free", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        connection.close()
