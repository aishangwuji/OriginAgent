"""PlanLibrary — cached means-ends reasoning for repeated desires."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.bdi.models import (
    DeliberationIntention,
    Desire,
    PlanMatch,
    PlanTemplate,
    now_iso,
)
from OriginAgent.utils.helpers import ensure_dir

_MIN_CONFIDENCE = 0.6

_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "and", "or", "not", "but", "if", "then", "else", "when",
    "i", "you", "he", "she", "it", "we", "they", "me", "him",
    "her", "us", "them", "my", "your", "his", "its", "our",
    "please", "can", "could", "would", "should", "will",
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这",
}


class PlanLibrary:
    """Pattern-matching cache for means-ends reasoning."""

    def __init__(self, workspace: Path, *, sqlite_store: Any = None, jsonl_fallback_enabled: bool = True) -> None:
        self.workspace = Path(workspace)
        self._dir = self.workspace / "memory" / "bdi"
        self._path = self._dir / "plans.jsonl"
        self._lock_path = self._dir / ".plans.lock"
        ensure_dir(self._dir)
        self._plans: dict[str, PlanTemplate] = {}
        self._sqlite = sqlite_store
        self._jsonl_fallback_enabled = jsonl_fallback_enabled
        self._load()

    def add(self, plan: PlanTemplate) -> None:
        self._plans[plan.plan_id] = plan
        self._save()

    def get(self, plan_id: str) -> PlanTemplate | None:
        return self._plans.get(plan_id)

    def list_plans(self) -> list[PlanTemplate]:
        return sorted(self._plans.values(), key=lambda p: -p.hit_count)

    def match(self, desire: Desire) -> PlanMatch | None:
        content_lower = desire.content.lower()
        best: tuple[PlanTemplate, float, list[str]] | None = None

        for plan in self._plans.values():
            matched: list[str] = []
            for kw in plan.keywords:
                if kw.lower() in content_lower:
                    matched.append(kw)
            if not matched:
                continue
            confidence = len(matched) / len(plan.keywords)
            if plan.hit_count > 10:
                confidence = min(1.0, confidence + 0.1)
            if best is None or confidence > best[1]:
                best = (plan, confidence, matched)

        if best is None or best[1] < _MIN_CONFIDENCE:
            return None

        plan = best[0].increment_hit()
        self._plans[plan.plan_id] = plan
        self._save()

        return PlanMatch(
            plan=plan,
            confidence=round(best[1], 2),
            matched_keywords=best[2],
            reasoning=f"Matched {len(best[2])}/{len(plan.keywords)} keywords",
        )

    def learn(
        self,
        *,
        desire: Desire,
        intention: DeliberationIntention,
        auto_keywords: bool = True,
    ) -> PlanTemplate:
        if auto_keywords:
            keywords = self._extract_keywords(desire.content)
        else:
            keywords = ()

        plan_id = f"plan_{intention.action}_{len(self._plans)}"

        for existing in self._plans.values():
            existing_set = set(existing.keywords)
            new_set = set(keywords)
            overlap = existing_set & new_set
            if len(overlap) > 0 and len(overlap) >= min(len(existing_set), len(new_set)) * 0.5:
                merged = existing.increment_hit()
                self._plans[existing.plan_id] = merged
                self._save()
                logger.debug("BDI: PlanLibrary merged similar plan — id={}", existing.plan_id)
                return merged

        plan = PlanTemplate(
            plan_id=plan_id, keywords=keywords,
            action=intention.action, scope=intention.scope,
            payload_template=dict(intention.payload),
            description=f"Auto-learned from desire: {desire.content[:80]}",
            hit_count=1, last_used_at=now_iso(),
            source_desire_id=desire.desire_id,
        )
        self._plans[plan_id] = plan
        self._save()
        logger.info("BDI: PlanLibrary learned new plan — id={} keywords={}", plan_id, keywords)
        return plan

    @staticmethod
    def _extract_keywords(text: str) -> tuple[str, ...]:
        tokens = re.findall(r"[\w一-鿿]+", text.lower())
        meaningful = [t for t in tokens if t not in _STOP_WORDS and len(t) >= 2]
        counter = Counter(meaningful)
        return tuple(k for k, _ in counter.most_common(5))

    def _load(self) -> None:
        if self._sqlite is not None:
            try:
                raw = self._sqlite.list_plans()
                for d in raw:
                    try:
                        plan = PlanTemplate.from_json(d)
                        self._plans[plan.plan_id] = plan
                    except Exception:
                        continue
                return
            except Exception:
                pass
        if not self._path.exists():
            return
        from OriginAgent.storage.jsonl_fallback import locked as _locked
        with _locked(self._sqlite, self._lock_path):
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        plan = PlanTemplate.from_json(json.loads(line))
                        self._plans[plan.plan_id] = plan
                    except Exception:
                        logger.warning("BDI: skipping corrupt plan line")

    def _save(self) -> None:
        ensure_dir(self._dir)
        if self._sqlite is not None:
            try:
                for plan in self._plans.values():
                    self._sqlite.add(plan)
            except Exception:
                logger.opt(exception=True).warning("plans: sqlite write failed, falling back to JSONL")
                self._jsonl_save()
                return
            if self._jsonl_fallback_enabled:
                try:
                    self._jsonl_save()
                except Exception:
                    logger.opt(exception=True).warning("plans: jsonl cold backup failed")
        else:
            self._jsonl_save()

    def _jsonl_save(self) -> None:
        """Full atomic JSONL rewrite with fsync (crash-safe fallback)."""
        ensure_dir(self._dir)
        from OriginAgent.storage.jsonl_fallback import locked as _locked
        with _locked(self._sqlite, self._lock_path):
            tmp = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=str(self._dir),
                delete=False, suffix=".tmp",
            )
            try:
                for plan in self._plans.values():
                    tmp.write(json.dumps(plan.to_json(), ensure_ascii=False) + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp.close()
                os.replace(tmp.name, str(self._path))
                try:
                    dir_fd = os.open(str(self._dir), os.O_RDONLY)
                    os.fsync(dir_fd)
                    os.close(dir_fd)
                except OSError:
                    pass
            except Exception:
                Path(tmp.name).unlink(missing_ok=True)
                raise
