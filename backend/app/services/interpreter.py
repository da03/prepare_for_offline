"""Local base interpreter (Qwen3-0.6B) used as the evidence-grounded answerer.

This is the single final author on the "generated from local sources" path.
It reuses the base GGUF that programasweights already caches on disk, loaded
directly via llama.cpp. It is deliberately constrained to answer ONLY from the
provided sources, and to emit the sentinel UNSUPPORTED when they do not answer
the question, so the app can abstain and queue for verification.
"""

from __future__ import annotations

import os
import re
import threading

from programasweights import cache

from ..config import get_settings

_THINK = re.compile(r"<think>.*?</think>", flags=re.DOTALL)

_SYSTEM = (
    "You are an offline travel assistant. Answer ONLY using the provided "
    "sources. Be concise and practical. If the sources do not contain the "
    "answer, reply with exactly: UNSUPPORTED. Do not invent facts."
)


class LocalInterpreter:
    def __init__(self) -> None:
        self._llm = None
        self._lock = threading.Lock()

    def _ensure_loaded(self):
        if self._llm is not None:
            return self._llm
        with self._lock:
            if self._llm is not None:
                return self._llm
            from llama_cpp import Llama

            model_path = cache.get_base_model_path(get_settings().interpreter)
            n_gpu_layers = int(os.environ.get("PAW_GPU_LAYERS", "-1"))
            self._llm = Llama(
                model_path=str(model_path),
                n_ctx=2048,
                n_gpu_layers=n_gpu_layers,
                verbose=False,
            )
            return self._llm

    def is_available(self) -> bool:
        try:
            cache.get_base_model_path(get_settings().interpreter)
            return True
        except Exception:
            return False

    def _chatml(self, user: str) -> str:
        return (
            f"<|im_start|>system\n{_SYSTEM} /no_think<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )

    def answer(self, prompt_body: str, *, max_tokens: int = 320) -> str:
        llm = self._ensure_loaded()
        prompt = self._chatml(prompt_body)
        with self._lock:
            out = llm.create_completion(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=0.0,
                stop=["<|im_end|>"],
            )
        text = out["choices"][0]["text"]
        text = _THINK.sub("", text).strip()
        return text


_interpreter: LocalInterpreter | None = None


def get_interpreter() -> LocalInterpreter:
    global _interpreter
    if _interpreter is None:
        _interpreter = LocalInterpreter()
    return _interpreter
