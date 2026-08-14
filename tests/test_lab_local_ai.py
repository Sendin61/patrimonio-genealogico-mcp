from __future__ import annotations

import asyncio
import json

from rob.lab.ai.base import AIProviderStatus
from rob.lab.ai.interpreter import interpret_research_request
from rob.lab.ai.llamacpp import LlamaCppRuntime


class FakeProvider:
    async def status(self) -> AIProviderStatus:
        return AIProviderStatus(True, "fake", "http://local", "fake-model")

    async def chat(self, messages, *, temperature=0.1, max_tokens=2048, json_schema=None):
        assert json_schema is not None
        return json.dumps(
            {
                "raw_request": "MODEL TRIED TO REWRITE THIS",
                "intent": "find_parents",
                "objectives": ["identificar padres"],
                "target_candidates": [
                    {
                        "value": "Andrés Quintás Varela",
                        "confidence": 0.91,
                        "reason": "posible normalización de barela",
                    }
                ],
                "people_mentions": [],
                "places": ["Arzúa"],
                "chronology": {
                    "year_from": None,
                    "year_to": None,
                    "around": 1800,
                    "confidence": 0.7,
                },
                "family_scope": ["parents"],
                "ocr_tolerant": True,
                "multipage_required": False,
                "uncertainties": ["barela podría ser Varela"],
                "proposed_actions": ["buscar variantes"],
            },
            ensure_ascii=False,
        )


def test_interpreter_preserves_user_text_and_forces_multipage() -> None:
    raw = "buscame los padres d andres quintas barela x arzua"
    result = asyncio.run(interpret_research_request(FakeProvider(), raw))
    assert result["raw_request"] == raw
    assert result["multipage_required"] is True
    assert result["target_candidates"][0]["value"] == "Andrés Quintás Varela"


def test_runtime_prefers_small_qwen_q4_model(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ROB_LAB_HOME", str(tmp_path / "ROB-Genealogy-Lab"))
    runtime = LlamaCppRuntime()
    (runtime.models_dir / "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf").write_bytes(b"x")
    preferred = runtime.models_dir / "Qwen3-4B-Q4_K_M.gguf"
    preferred.write_bytes(b"x")
    assert runtime.model_path() == preferred.resolve()
