"""Thin wrapper around Laya, used only as the semantic fallback signal.

Called once per test *file/suite*, never per individual test, to keep call
volume bounded regardless of repo size (see README "Performance").
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

os.environ.setdefault("HF_HUB_OFFLINE", "1")

QUESTIONS = {
    "affected": {
        "type": "noul",
        "instructions": (
            "Given a code diff description and a test suite's name/purpose, "
            "does the diff plausibly affect this test suite's behavior?"
        ),
    },
}


@dataclass
class ClassificationResult:
    suite_id: str
    probability: float


class LayaClassifier:
    def __init__(self, model_path: str, device: str | None = None, threshold: float = 0.5):
        self.model_path = model_path
        self.device = device
        self.threshold = threshold
        self._agent = None

    def _load(self):
        if self._agent is None:
            import laya

            self._agent = laya.load(self.model_path, device=self.device)
        return self._agent

    def is_affected(self, diff_text: str, suite_title: str) -> ClassificationResult:
        agent = self._load()
        state = f"Diff:\n{diff_text}\n\nTest suite: {suite_title}"
        result = agent.predict(state, QUESTIONS)
        prob = float(result["answers"]["affected"]["noul"])
        return ClassificationResult(suite_id=suite_title, probability=prob)

    def filter_suites(
        self, diff_text: str, suites: dict[str, str]
    ) -> dict[str, ClassificationResult]:
        """suites: suite_id -> title/description text.
        Returns only the suites classified as affected (probability >= threshold)."""
        out: dict[str, ClassificationResult] = {}
        for suite_id, title in suites.items():
            res = self.is_affected(diff_text, title)
            if res.probability >= self.threshold:
                out[suite_id] = res
        return out
