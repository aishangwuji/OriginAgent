# CS-009 SkillBootstrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `SkillBootstrapper` service that mines repeated action patterns from `ThoughtJournalEntry` / `ReflectionRecord` streams and generates reviewable `SkillArtifact` candidates through the governed evolution pipeline.

**Architecture:** A periodic scanner reads nearline thought artifacts, clusters similar action sequences across sessions using a deterministic fingerprint (TF-IDF on tool names + parameter shapes + correction patterns), and when a pattern exceeds configurable repeat thresholds, synthesizes a `CompiledProposalBundle` with target_type="skill" that drops into the existing `MetaProgrammingEngine` compilation store and curator review path.

**Tech Stack:** Python 3.11+, asyncio, existing `skill_artifacts.py` / `skill_lifecycle.py` / `meta_programming.py` / `evolution.py` / `meta_cognition_patterns.py`.

## Global Constraints

- Frozen dataclasses for all new model objects (follow `meta_cognition_models.py` pattern).
- All new code gets tests (pytest, `asyncio_mode="auto"`).
- No modification to existing `MetaCognitionReflector` or `MetaCognitionRuntime` — bootstrapper is additive.
- Config toggle in `MetaCognitionConfig` (`skill_bootstrapper_enabled`, default True).
- All outputs flow through governed evolution (review path), never auto-applied.
- Follow existing file naming: `OriginAgent/agent/skill_bootstrapper*`.
- Thread-safe JSONL persistence (follow `JsonlMetaCognitionAuditLedger._append` pattern).
- ruff lint `--select F` clean.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `OriginAgent/agent/skill_bootstrapper.py` | Main service: pattern scanner, repeat detector, fingerprint builder, candidate compiler |
| `OriginAgent/agent/skill_bootstrapper_models.py` | `ActionTraceDigest`, `RepeatedPattern`, `SkillCandidate` frozen dataclasses |
| `tests/agent/test_skill_bootstrapper.py` | Full test suite |
| `OriginAgent/config/schema.py` (modify) | Add `skill_bootstrapper_enabled`, `skill_bootstrapper_min_repeats`, `skill_bootstrapper_min_confidence` |
| `OriginAgent/agent/agent_loop_components.py` (modify) | Wire bootstrapper |

### Interfaces Between Tasks

```
Task 1 (models)          → Task 2 (fingerprint)    → Task 3 (scanner)
                              ↓                           ↓
                         Task 2 produces              Task 3 consumes
                         ActionTraceDigest           ThoughtJournalEntry +
                                                     ReflectionRecord

Task 4 (candidate gen)   ← Task 3 (scanner)         → Task 5 (integration)
Task 4 produces          Task 3 produces             Task 5 wires into
SkillCandidate +          RepeatedPattern            agent_loop_components
CompiledProposalBundle
```

---

### Task 1: SkillBootstrapperModels — data contracts

**Files:**
- Create: `OriginAgent/agent/skill_bootstrapper_models.py`

**Interfaces:**
- Consumes: Nothing (first task)
- Produces: `ActionTraceDigest`, `RepeatedPattern`, `SkillCandidate` — used by Tasks 2-4

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_skill_bootstrapper.py` with model tests:

```python
from OriginAgent.agent.skill_bootstrapper_models import (
    ActionTraceDigest,
    RepeatedPattern,
    SkillCandidate,
)


def test_action_trace_digest_minimal():
    d = ActionTraceDigest(digest_id="d1", session_key="s1", tool_sequence=["read_file", "grep"])
    assert d.digest_id == "d1"
    assert d.tool_sequence == ["read_file", "grep"]
    assert d.fingerprint != ""


def test_action_trace_digest_empty_sequence():
    d = ActionTraceDigest(digest_id="d2", session_key="s2", tool_sequence=[])
    assert d.fingerprint == ""  # empty sequence → empty fingerprint


def test_repeated_pattern_minimal():
    rp = RepeatedPattern(
        pattern_id="rp1",
        fingerprint_hash="abc123",
        tool_signature="read_file+grep",
        repeat_count=3,
        session_keys=["s1", "s2", "s3"],
    )
    assert rp.repeat_count == 3
    assert rp.confidence == 0.0


def test_repeated_pattern_confidence():
    rp = RepeatedPattern(
        pattern_id="rp2",
        fingerprint_hash="def456",
        tool_signature="web_search+web_fetch",
        repeat_count=5,
        session_keys=["s1"] * 5,
        confidence=0.85,
    )
    assert rp.confidence == 0.85


def test_skill_candidate_minimal():
    sc = SkillCandidate(
        candidate_id="sc1",
        pattern_id="rp1",
        skill_name="diagnose-cpu-spike",
        description="Auto-detected repeated pattern",
        body="Steps to diagnose CPU spike",
        confidence=0.8,
    )
    assert sc.candidate_id == "sc1"
    assert sc.governance_path == "review_required"


def test_skill_candidate_defaults():
    sc = SkillCandidate(candidate_id="sc2", pattern_id="rp2", skill_name="test-skill",
                        description="test", body="test")
    assert not sc.dangerous_tools
    assert sc.verification_plan == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agent/test_skill_bootstrapper.py -v`
Expected: 6 FAIL — all "cannot import name" errors

- [ ] **Step 3: Write the model module**

Create `OriginAgent/agent/skill_bootstrapper_models.py`:

```python
"""Data contracts for the SkillBootstrapper pattern miner.

ActionTraceDigest  — one normalised action trace from a turn/reflection.
RepeatedPattern   — a fingerprint that has been seen N times across sessions.
SkillCandidate    — a compiled candidate ready for the governed evolution path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _normalize_str_list(value: Any, *, limit: int = 32, max_chars: int = 120) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        t = _normalize_text(item, max_chars=max_chars)
        if t:
            out.append(t)
            if len(out) >= limit:
                break
    return out


def _normalize_float(value: Any, *, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(v, 1.0))


def _filter_known_fields(raw: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("payload must be an object")
    return {k: v for k, v in raw.items() if k in allowed}


def _build_fingerprint(tool_sequence: list[str], param_preview: str = "") -> str:
    if not tool_sequence:
        return ""
    raw = json.dumps([tool_sequence, param_preview], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ActionTraceDigest:
    """A normalised trace of one action sequence extracted from a journal/reflection."""

    digest_id: str
    session_key: str
    tool_sequence: list[str] = field(default_factory=list)
    param_preview: str = ""
    correction_flag: bool = False
    task_reference: str = ""
    created_at: str = field(default_factory=_utcnow_iso)

    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        fp = _build_fingerprint(
            _normalize_str_list(self.tool_sequence, limit=16, max_chars=80),
            _normalize_text(self.param_preview, max_chars=160),
        )
        object.__setattr__(self, "fingerprint", fp)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ActionTraceDigest":
        allowed = set(cls.__dataclass_fields__.keys()) | {"fingerprint"}
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("digest_id", "")
        payload.setdefault("session_key", "")
        payload.setdefault("created_at", _utcnow_iso())
        return cls(**payload)


@dataclass(frozen=True)
class RepeatedPattern:
    """A tool-sequence fingerprint that has been observed multiple times."""

    pattern_id: str
    fingerprint_hash: str
    tool_signature: str
    repeat_count: int = 1
    session_keys: list[str] = field(default_factory=list)
    sample_digest_ids: list[str] = field(default_factory=list)
    first_seen_at: str = field(default_factory=_utcnow_iso)
    last_seen_at: str = field(default_factory=_utcnow_iso)
    confidence: float = 0.0
    has_correction: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "RepeatedPattern":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("pattern_id", "")
        payload.setdefault("fingerprint_hash", "")
        payload.setdefault("tool_signature", "")
        return cls(**payload)


@dataclass(frozen=True)
class SkillCandidate:
    """A compiled skill candidate ready for the governed evolution review path."""

    candidate_id: str
    pattern_id: str
    skill_name: str
    description: str
    body: str
    confidence: float = 0.0
    governance_path: str = "review_required"
    dangerous_tools: list[str] = field(default_factory=list)
    verification_plan: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow_iso)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "SkillCandidate":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("candidate_id", "")
        payload.setdefault("pattern_id", "")
        payload.setdefault("skill_name", "")
        payload.setdefault("description", "")
        payload.setdefault("body", "")
        return cls(**payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agent/test_skill_bootstrapper.py::TestActionTraceDigest tests/agent/test_skill_bootstrapper.py::TestRepeatedPattern tests/agent/test_skill_bootstrapper.py::TestSkillCandidate -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/agent/skill_bootstrapper_models.py tests/agent/test_skill_bootstrapper.py
git commit -m "feat(cs-009): add SkillBootstrapperModels data contracts"
```

---

### Task 2: Fingerprint Builder — deterministically fingerprint action traces

**Files:**
- Modify: `OriginAgent/agent/skill_bootstrapper.py` (create)
- Modify: `tests/agent/test_skill_bootstrapper.py` (append)

**Interfaces:**
- Consumes: `ActionTraceDigest` from Task 1
- Produces: `build_fingerprint(digest) → str`, `fingerprint_from_tools(tools, params) → str`

- [ ] **Step 1: Write the failing tests**

Append to `test_skill_bootstrapper.py`:

```python
from OriginAgent.agent.skill_bootstrapper import build_fingerprint, fingerprint_from_tools
from OriginAgent.agent.skill_bootstrapper_models import ActionTraceDigest


def test_build_fingerprint_consistency():
    d1 = ActionTraceDigest(digest_id="a", session_key="s1",
                           tool_sequence=["read_file", "grep"])
    d2 = ActionTraceDigest(digest_id="b", session_key="s2",
                           tool_sequence=["read_file", "grep"])
    fp1 = build_fingerprint(d1)
    fp2 = build_fingerprint(d2)
    assert fp1 == fp2  # same tools → same fingerprint across sessions


def test_build_fingerprint_different_tools():
    d1 = ActionTraceDigest(digest_id="a", session_key="s1",
                           tool_sequence=["read_file"])
    d2 = ActionTraceDigest(digest_id="b", session_key="s2",
                           tool_sequence=["grep"])
    assert build_fingerprint(d1) != build_fingerprint(d2)


def test_build_fingerprint_empty():
    d = ActionTraceDigest(digest_id="c", session_key="s3", tool_sequence=[])
    assert build_fingerprint(d) == ""


def test_fingerprint_from_tools():
    fp = fingerprint_from_tools(["web_search", "web_fetch"], "query=error")
    assert len(fp) == 32  # sha256 hex[:32]


def test_fingerprint_from_tools_empty():
    assert fingerprint_from_tools([], "") == ""
```

- [ ] **Step 2: Run new tests**

Run: `pytest tests/agent/test_skill_bootstrapper.py::test_build_fingerprint_consistency tests/agent/test_skill_bootstrapper.py::test_build_fingerprint_different_tools tests/agent/test_skill_bootstrapper.py::test_build_fingerprint_empty tests/agent/test_skill_bootstrapper.py::test_fingerprint_from_tools tests/agent/test_skill_bootstrapper.py::test_fingerprint_from_tools_empty -v`
Expected: 5 FAIL (module not found)

- [ ] **Step 3: Write the implementation**

Create `OriginAgent/agent/skill_bootstrapper.py`:

```python
"""SkillBootstrapper — mine repeated action patterns and compile skill candidates.

Scans nearline thought artifacts (journal entries, reflection records) for
repeated tool-usage sequences, fingerprints them deterministically, and when
a pattern exceeds configurable thresholds synthesises a ``SkillCandidate``
that flows into the governed evolution review path.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from OriginAgent.agent.skill_bootstrapper_models import (
    ActionTraceDigest,
    _build_fingerprint,
    _normalize_str_list,
    _normalize_text,
)


def _new_id(prefix: str = "sb") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def build_fingerprint(digest: ActionTraceDigest) -> str:
    """Return the deterministic fingerprint for an ``ActionTraceDigest``.

    Delegates to the model's own fingerprint computation so the result is
    always consistent with ``ActionTraceDigest.fingerprint``.
    """
    return digest.fingerprint


def fingerprint_from_tools(tool_sequence: list[str], param_preview: str = "") -> str:
    """Compute a deterministic fingerprint directly from a tool list + parameter preview."""
    tools = _normalize_str_list(tool_sequence, limit=16, max_chars=80)
    if not tools:
        return ""
    preview = _normalize_text(param_preview, max_chars=160)
    raw = json.dumps([tools, preview], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def tool_signature(tool_sequence: list[str]) -> str:
    """A human-readable collapsed signature like ``"read_file+grep"``."""
    return "+".join(_normalize_str_list(tool_sequence, limit=16, max_chars=80))
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/agent/test_skill_bootstrapper.py::test_build_fingerprint_consistency tests/agent/test_skill_bootstrapper.py::test_build_fingerprint_different_tools tests/agent/test_skill_bootstrapper.py::test_build_fingerprint_empty tests/agent/test_skill_bootstrapper.py::test_fingerprint_from_tools tests/agent/test_skill_bootstrapper.py::test_fingerprint_from_tools_empty -v`
Expected: 5 PASS

- [ ] **Step 5: Run all Task 1+2 tests**

Run: `pytest tests/agent/test_skill_bootstrapper.py -v`
Expected: 12 PASS

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/agent/skill_bootstrapper.py tests/agent/test_skill_bootstrapper.py
git commit -m "feat(cs-009): add fingerprint builder for action traces"
```

---

### Task 3: Pattern Scanner — read thought artifacts and find repeats

**Files:**
- Modify: `OriginAgent/agent/skill_bootstrapper.py` (add `SkillBootstrapperScanner`)
- Modify: `tests/agent/test_skill_bootstrapper.py` (append scanner tests)

**Interfaces:**
- Consumes: `ActionTraceDigest`, `RepeatedPattern` (Task 1), `build_fingerprint` (Task 2)
- Produces: `SkillBootstrapperScanner` class with `scan()` method

- [ ] **Step 1: Write the failing tests**

Append to `test_skill_bootstrapper.py`:

```python
from datetime import datetime, timezone
from pathlib import Path
from OriginAgent.agent.skill_bootstrapper import SkillBootstrapperScanner
from OriginAgent.agent.skill_bootstrapper_models import ActionTraceDigest, RepeatedPattern


def test_scanner_empty():
    scanner = SkillBootstrapperScanner()
    patterns = scanner.scan([])  # no digests
    assert patterns == []


def test_scanner_single_digest_no_repeat():
    scanner = SkillBootstrapperScanner()
    digests = [
        ActionTraceDigest(digest_id="d1", session_key="s1",
                          tool_sequence=["read_file"]),
    ]
    patterns = scanner.scan(digests, min_repeats=2)
    assert len(patterns) == 0  # need at least 2


def test_scanner_two_matching():
    scanner = SkillBootstrapperScanner()
    digests = [
        ActionTraceDigest(digest_id="d1", session_key="s1",
                          tool_sequence=["read_file", "grep"]),
        ActionTraceDigest(digest_id="d2", session_key="s2",
                          tool_sequence=["read_file", "grep"]),
    ]
    patterns = scanner.scan(digests, min_repeats=2)
    assert len(patterns) == 1
    assert patterns[0].repeat_count == 2


def test_scanner_three_sessions():
    scanner = SkillBootstrapperScanner()
    digests = [
        ActionTraceDigest(digest_id="d1", session_key="s1",
                          tool_sequence=["web_search"]),
        ActionTraceDigest(digest_id="d2", session_key="s2",
                          tool_sequence=["web_search"]),
        ActionTraceDigest(digest_id="d3", session_key="s3",
                          tool_sequence=["web_search"]),
    ]
    patterns = scanner.scan(digests, min_repeats=2)
    assert len(patterns) == 1
    assert patterns[0].repeat_count == 3


def test_scanner_two_distinct_patterns():
    scanner = SkillBootstrapperScanner()
    digests = [
        ActionTraceDigest(digest_id="d1", session_key="s1",
                          tool_sequence=["read_file"]),
        ActionTraceDigest(digest_id="d2", session_key="s2",
                          tool_sequence=["read_file"]),
        ActionTraceDigest(digest_id="d3", session_key="s3",
                          tool_sequence=["grep"]),
        ActionTraceDigest(digest_id="d4", session_key="s4",
                          tool_sequence=["grep"]),
    ]
    patterns = scanner.scan(digests, min_repeats=2)
    assert len(patterns) == 2


def test_scanner_respects_min_repeats():
    scanner = SkillBootstrapperScanner()
    digests = [
        ActionTraceDigest(digest_id="d1", session_key="s1",
                          tool_sequence=["read_file"]),
        ActionTraceDigest(digest_id="d2", session_key="s2",
                          tool_sequence=["read_file"]),
        ActionTraceDigest(digest_id="d3", session_key="s3",
                          tool_sequence=["read_file"]),
    ]
    assert len(scanner.scan(digests, min_repeats=1)) == 1
    assert len(scanner.scan(digests, min_repeats=2)) == 1
    assert len(scanner.scan(digests, min_repeats=4)) == 0  # 3 < 4


def test_scanner_dedupe_fingerprint():
    scanner = SkillBootstrapperScanner()
    digests = [
        ActionTraceDigest(digest_id="d1", session_key="s1",
                          tool_sequence=["read_file"]),
        ActionTraceDigest(digest_id="d2", session_key="s2",
                          tool_sequence=["read_file"]),
        ActionTraceDigest(digest_id="d3", session_key="s3",
                          tool_sequence=["read_file"]),
    ]
    patterns = scanner.scan(digests, min_repeats=2)
    assert patterns[0].fingerprint_hash == digests[0].fingerprint
```

- [ ] **Step 2: Run new scanner tests**

Run: `pytest tests/agent/test_skill_bootstrapper.py -v -k "scanner"`
Expected: 7 FAIL (class not defined)

- [ ] **Step 3: Write the scanner**

Append to `OriginAgent/agent/skill_bootstrapper.py`:

```python
from collections import defaultdict

from OriginAgent.agent.skill_bootstrapper_models import (
    ActionTraceDigest,
    RepeatedPattern,
    _utcnow_iso,
)


class SkillBootstrapperScanner:
    """Scan digests and group by fingerprint to find repeated patterns."""

    def scan(
        self,
        digests: list[ActionTraceDigest],
        *,
        min_repeats: int = 3,
        min_confidence: float = 0.5,
    ) -> list[RepeatedPattern]:
        """Group digests by fingerprint and return patterns exceeding thresholds."""
        groups: dict[str, list[ActionTraceDigest]] = defaultdict(list)
        for d in digests:
            fp = build_fingerprint(d)
            if fp:
                groups[fp].append(d)

        patterns: list[RepeatedPattern] = []
        for fp, matched in groups.items():
            if len(matched) < min_repeats:
                continue
            session_keys = list({d.session_key for d in matched})
            has_correction = any(d.correction_flag for d in matched)
            # confidence: starts at 0.5, +0.1 per repeat (cap 0.95)
            confidence = min(0.95, 0.5 + (len(matched) - 1) * 0.1)
            if confidence < min_confidence:
                continue

            sample = matched[0]
            patterns.append(RepeatedPattern(
                pattern_id=_new_id("rp"),
                fingerprint_hash=fp,
                tool_signature=tool_signature(sample.tool_sequence),
                repeat_count=len(matched),
                session_keys=session_keys,
                sample_digest_ids=[d.digest_id for d in matched],
                first_seen_at=min(d.created_at for d in matched),
                last_seen_at=max(d.created_at for d in matched),
                confidence=confidence,
                has_correction=has_correction,
            ))
        return patterns
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/agent/test_skill_bootstrapper.py -v -k "scanner"`
Expected: 7 PASS

- [ ] **Step 5: Run all tests so far**

Run: `pytest tests/agent/test_skill_bootstrapper.py -v`
Expected: 19 PASS

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/agent/skill_bootstrapper.py tests/agent/test_skill_bootstrapper.py
git commit -m "feat(cs-009): add SkillBootstrapperScanner pattern detector"
```

---

### Task 4: Candidate Compiler — RepeatedPattern → SkillCandidate → CompiledProposalBundle

**Files:**
- Modify: `OriginAgent/agent/skill_bootstrapper.py` (add `SkillCandidateCompiler`)
- Modify: `tests/agent/test_skill_bootstrapper.py` (append compiler tests)

**Interfaces:**
- Consumes: `RepeatedPattern` (Task 3), `SkillCandidate` (Task 1)
- Produces: `SkillCandidateCompiler.compile() → SkillCandidate`, `compile_to_proposal_bundle(candidate) → CompiledProposalBundle`

- [ ] **Step 1: Write the failing tests**

Append to `test_skill_bootstrapper.py`:

```python
from OriginAgent.agent.skill_bootstrapper import SkillCandidateCompiler
from OriginAgent.agent.skill_bootstrapper_models import RepeatedPattern, SkillCandidate


def test_compiler_minimal():
    compiler = SkillCandidateCompiler()
    pattern = RepeatedPattern(
        pattern_id="rp1",
        fingerprint_hash="abc",
        tool_signature="read_file+grep",
        repeat_count=3,
        session_keys=["s1", "s2", "s3"],
        confidence=0.7,
    )
    candidate = compiler.compile(pattern)
    assert candidate is not None
    assert candidate.pattern_id == "rp1"
    assert candidate.confidence == 0.7
    assert "read-file-grep" in candidate.skill_name


def test_compiler_skill_name_format():
    compiler = SkillCandidateCompiler()
    pattern = RepeatedPattern(
        pattern_id="rp2",
        fingerprint_hash="def",
        tool_signature="web_search+web_fetch",
        repeat_count=5,
        session_keys=["s1"] * 5,
        confidence=0.85,
    )
    candidate = compiler.compile(pattern)
    assert candidate.skill_name == "web-search-web-fetch"
    assert candidate.governance_path == "review_required"


def test_compiler_dangerous_tools_detected():
    compiler = SkillCandidateCompiler()
    pattern = RepeatedPattern(
        pattern_id="rp3",
        fingerprint_hash="ghi",
        tool_signature="exec",
        repeat_count=3,
        session_keys=["s1", "s2", "s3"],
        confidence=0.7,
    )
    candidate = compiler.compile(pattern)
    assert candidate is not None
    assert "exec" in candidate.dangerous_tools


def test_compiler_low_confidence_returns_none():
    compiler = SkillCandidateCompiler()
    pattern = RepeatedPattern(
        pattern_id="rp4",
        fingerprint_hash="jkl",
        tool_signature="read_file",
        repeat_count=2,
        session_keys=["s1", "s2"],
        confidence=0.3,
    )
    candidate = compiler.compile(pattern, min_confidence=0.5)
    assert candidate is None


def test_compile_to_proposal_bundle():
    from OriginAgent.agent.skill_bootstrapper import compile_to_proposal_bundle
    candidate = SkillCandidate(
        candidate_id="sc1", pattern_id="rp1",
        skill_name="diagnose-cpu",
        description="Auto-detected: diagnose CPU spike",
        body="1. Run top\n2. Check logs",
        confidence=0.8,
    )
    bundle = compile_to_proposal_bundle(candidate)
    assert bundle is not None
    assert bundle.target_type == "skill"
    assert bundle.target_key == "diagnose-cpu"
    assert bundle.risk_level == "medium"
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/agent/test_skill_bootstrapper.py -v -k "compiler"`
Expected: 5 FAIL

- [ ] **Step 3: Write the compiler**

Append to `OriginAgent/agent/skill_bootstrapper.py`:

```python
import re

from OriginAgent.agent.skill_bootstrapper_models import (
    RepeatedPattern,
    SkillCandidate,
    _utcnow_iso,
)

# Tools that always make a candidate "review_required" / "dangerous"
_DANGEROUS_TERMS_RE = re.compile(
    r"(?i)(?<![a-z0-9_])"
    r"(?:exec|shell|command|write_file|edit_file|cron|spawn|delete|remove|rm|"
    r"powershell|cmd\.exe|message|send_message)"
    r"(?![a-z0-9_])"
)


def _skill_name_from_signature(signature: str) -> str:
    """Convert ``"read_file+grep"`` → ``"read-file-grep"``."""
    return signature.replace("+", "-").replace("_", "-").lower().strip("-") or "unnamed-pattern"


def _build_skill_body(pattern: RepeatedPattern) -> str:
    """Generate a minimal skill body from the pattern's sample digests.

    Phase 1 uses a template; future phases can use an LLM call.
    """
    lines = [
        f"# {_skill_name_from_signature(pattern.tool_signature)}",
        "",
        f"Auto-detected pattern (seen {pattern.repeat_count}x across "
        f"{len(pattern.session_keys)} session(s)).",
        "",
        "## Steps",
        "",
    ]
    for i, tool in enumerate(pattern.tool_signature.split("+"), 1):
        lines.append(f"{i}. Use the `{tool}` tool")
    lines.extend([
        "",
        "## When to Use",
        "",
        "This skill was bootstrapped from repeated usage patterns.",
    ])
    return "\n".join(lines)


class SkillCandidateCompiler:
    """Compile a ``RepeatedPattern`` into a ``SkillCandidate``.

    Filters low-confidence patterns and flags dangerous tool usage for
    stricter review.
    """

    def compile(
        self,
        pattern: RepeatedPattern,
        *,
        min_confidence: float = 0.5,
    ) -> SkillCandidate | None:
        if pattern.confidence < min_confidence:
            return None

        signature = pattern.tool_signature
        skill_name = _skill_name_from_signature(signature)

        # check for dangerous tools
        dangerous: list[str] = []
        for tool in signature.split("+"):
            if _DANGEROUS_TERMS_RE.search(tool):
                dangerous.append(tool)

        body = _build_skill_body(pattern)
        governance = "danger_review" if dangerous else "review_required"

        return SkillCandidate(
            candidate_id=_new_id("sc"),
            pattern_id=pattern.pattern_id,
            skill_name=skill_name,
            description=f"Auto-detected repeated pattern: {signature} "
                        f"(seen {pattern.repeat_count}x, "
                        f"confidence={pattern.confidence:.2f})",
            body=body,
            confidence=pattern.confidence,
            governance_path=governance,
            dangerous_tools=dangerous,
            verification_plan=[f"Verify {t} usage is correct" for t in signature.split("+")],
        )


def compile_to_proposal_bundle(
    candidate: SkillCandidate,
    *,
    bundle_id: str | None = None,
) -> CompiledProposalBundle:
    """Wrap a ``SkillCandidate`` into a ``CompiledProposalBundle`` for the
    governed evolution pipeline."""
    from OriginAgent.agent.meta_programming import COMPILER_VERSION, CompiledProposalBundle

    return CompiledProposalBundle(
        bundle_id=bundle_id or _new_id("bundle"),
        source_pattern_id=candidate.pattern_id,
        source_signal_id="",
        target_type="skill",
        target_key=candidate.skill_name,
        input_summary_hash=_stable_hash([
            candidate.skill_name, candidate.body[:200],
        ]),
        compiler_version=COMPILER_VERSION,
        summary=candidate.description,
        hypothesis=f"Repeated pattern ({candidate.confidence:.2f} confidence) "
                   f"suggests a reusable skill",
        risk_level="high" if candidate.dangerous_tools else "medium",
        review_mode="danger_review" if candidate.dangerous_tools else "review_required",
        evidence_sources=[{"preview": candidate.body[:200]}],
        payload={"skill_name": candidate.skill_name, "body_preview": candidate.body[:500]},
        review_only=bool(candidate.dangerous_tools),
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/agent/test_skill_bootstrapper.py -v -k "compiler"`
Expected: 5 PASS

- [ ] **Step 5: Run all tests so far**

Run: `pytest tests/agent/test_skill_bootstrapper.py -v`
Expected: 24 PASS

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/agent/skill_bootstrapper.py tests/agent/test_skill_bootstrapper.py
git commit -m "feat(cs-009): add SkillCandidateCompiler and proposal bundle builder"
```

---

### Task 5: Integration — config + wiring

**Files:**
- Modify: `OriginAgent/config/schema.py`
- Modify: `OriginAgent/agent/agent_loop_components.py`

**Interfaces:**
- Consumes: `SkillBootstrapperScanner`, `SkillCandidateCompiler` (Task 4)
- Produces: Wired service instance on loop

- [ ] **Step 1: Write config test**

Run: `python -c "from OriginAgent.config.schema import MetaCognitionConfig; c = MetaCognitionConfig(); assert hasattr(c, 'skill_bootstrapper_enabled')"`
Expected: FAIL (field doesn't exist yet)

- [ ] **Step 2: Add config fields**

In `OriginAgent/config/schema.py`, inside `MetaCognitionConfig`, after the `regulator_enabled` field (around line 1248), add:

```python
    skill_bootstrapper_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "skillBootstrapperEnabled",
            "skill_bootstrapper_enabled",
        ),
        serialization_alias="skillBootstrapperEnabled",
    )
    skill_bootstrapper_min_repeats: int = Field(
        default=3,
        ge=2,
        le=100,
        validation_alias=AliasChoices(
            "skillBootstrapperMinRepeats",
            "skill_bootstrapper_min_repeats",
        ),
        serialization_alias="skillBootstrapperMinRepeats",
    )
    skill_bootstrapper_min_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "skillBootstrapperMinConfidence",
            "skill_bootstrapper_min_confidence",
        ),
        serialization_alias="skillBootstrapperMinConfidence",
    )
```

- [ ] **Step 3: Verify config**

Run: `python -c "from OriginAgent.config.schema import MetaCognitionConfig; c = MetaCognitionConfig(); assert c.skill_bootstrapper_enabled; assert c.skill_bootstrapper_min_repeats == 3; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Wire in agent_loop_components.py**

In `OriginAgent/agent/agent_loop_components.py`, add the import at the top:

```python
from OriginAgent.agent.skill_bootstrapper import (
    SkillBootstrapperScanner,
    SkillCandidateCompiler,
)
```

Then after the `MetaCognitionRegulator` wire-up (around line 573), add:

```python
    # ── SkillBootstrapper (CS-009) ─────────────────────────────────
    _bs_enabled = getattr(values.get("_meta_cognition_config"), "skill_bootstrapper_enabled", True)
    _scanner: SkillBootstrapperScanner | None = None
    _compiler: SkillCandidateCompiler | None = None
    if _bs_enabled:
        _scanner = SkillBootstrapperScanner()
        _compiler = SkillCandidateCompiler()
    values["_skill_bootstrapper_scanner"] = _scanner
    values["_skill_bootstrapper_compiler"] = _compiler
```

- [ ] **Step 5: Verify wiring imports**

Run: `python -c "from OriginAgent.agent.agent_loop_components import *; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/config/schema.py OriginAgent/agent/agent_loop_components.py
git commit -m "feat(cs-009): add SkillBootstrapper config and wiring"
```

---

### Task 6: Full Integration Test — end-to-end pipeline

**Files:**
- Modify: `tests/agent/test_skill_bootstrapper.py` (append integration test)

- [ ] **Step 1: Write the integration test**

Append to `test_skill_bootstrapper.py`:

```python
from pathlib import Path
from OriginAgent.agent.skill_bootstrapper import (
    SkillBootstrapperScanner,
    SkillCandidateCompiler,
    compile_to_proposal_bundle,
)
from OriginAgent.agent.skill_bootstrapper_models import ActionTraceDigest
from OriginAgent.agent.meta_programming import CompiledProposalBundle


def test_end_to_end_pipeline():
    """Simulate the full pipeline: digests → scan → compile → bundle."""
    scanner = SkillBootstrapperScanner()
    compiler = SkillCandidateCompiler()

    # 5 matching digests (read_file + grep pattern)
    digests = [
        ActionTraceDigest(digest_id=f"d{i}", session_key=f"s{i % 3}",
                          tool_sequence=["read_file", "grep"])
        for i in range(5)
    ]
    # Add 2 different digests that should not match
    digests.append(ActionTraceDigest(digest_id="d5", session_key="s3",
                                     tool_sequence=["web_search"]))

    # Scan
    patterns = scanner.scan(digests, min_repeats=2)
    assert len(patterns) == 1  # only one repeated pattern
    assert patterns[0].tool_signature == "read_file+grep"
    assert patterns[0].repeat_count == 5

    # Compile
    candidate = compiler.compile(patterns[0])
    assert candidate is not None
    assert candidate.skill_name == "read-file-grep"
    assert candidate.confidence >= 0.5

    # Bundle
    bundle = compile_to_proposal_bundle(candidate)
    assert isinstance(bundle, CompiledProposalBundle)
    assert bundle.target_type == "skill"
    assert bundle.target_key == "read-file-grep"
    assert bundle.review_mode == "review_required"


def test_end_to_end_with_dangerous_tools():
    """Pipeline with a dangerous tool should set stricter review."""
    scanner = SkillBootstrapperScanner()
    compiler = SkillCandidateCompiler()

    digests = [
        ActionTraceDigest(digest_id=f"d{i}", session_key=f"s{i}",
                          tool_sequence=["exec", "grep"])
        for i in range(4)
    ]
    patterns = scanner.scan(digests, min_repeats=2)
    assert len(patterns) == 1

    candidate = compiler.compile(patterns[0])
    assert candidate is not None
    assert "exec" in candidate.dangerous_tools
    assert candidate.governance_path == "danger_review"

    bundle = compile_to_proposal_bundle(candidate)
    assert bundle.risk_level == "high"
    assert bundle.review_mode == "danger_review"


def test_end_to_end_below_threshold():
    """Pattern below min_repeats should not produce a candidate."""
    scanner = SkillBootstrapperScanner()
    digests = [
        ActionTraceDigest(digest_id="d1", session_key="s1",
                          tool_sequence=["read_file"]),
        ActionTraceDigest(digest_id="d2", session_key="s2",
                          tool_sequence=["grep"]),
    ]
    patterns = scanner.scan(digests, min_repeats=3)
    assert len(patterns) == 0
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/agent/test_skill_bootstrapper.py -v -k "end_to_end"`
Expected: 3 PASS

- [ ] **Step 3: Run ALL tests**

Run: `pytest tests/agent/test_skill_bootstrapper.py -v`
Expected: 27 PASS

- [ ] **Step 4: Run existing full suite (no regression)**

Run: `python -m pytest tests/agent/test_meta_cognition_runtime.py tests/agent/test_meta_programming.py -v --tb=short 2>&1 | tail -20`
Expected: Existing tests all pass

- [ ] **Step 5: Run ruff check**

Run: `ruff check OriginAgent/agent/skill_bootstrapper.py OriginAgent/agent/skill_bootstrapper_models.py --select F`
Expected: All checks passed

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/agent/skill_bootstrapper.py OriginAgent/agent/skill_bootstrapper_models.py \
      tests/agent/test_skill_bootstrapper.py \
      OriginAgent/config/schema.py OriginAgent/agent/agent_loop_components.py
git commit -m "feat(cs-009): SkillBootstrapper — repeat pattern miner and skill candidate compiler"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- Repeated action pattern detection across sessions → Task 3 (Scanner)
- Deterministic fingerprinting → Task 2 (Fingerprint builder)
- Confidence derivation from repeat count → Task 3 (confidence formula)
- SkillBody generation → Task 4 (`_build_skill_body`)
- Dangerous tool detection → Task 4 (`_DANGEROUS_TERMS_RE`)
- Flow into governed evolution → Task 4 (`compile_to_proposal_bundle`)
- Config toggle → Task 5
- All outputs review-only, never auto-applied → Task 4 (governance_path, review_mode)

**2. Placeholder scan:** No "TBD", "TODO", "implement later", "add appropriate error handling" found.

**3. Type consistency:** `ActionTraceDigest.fingerprint` (computed in `__post_init__`) matches `build_fingerprint()` and `fingerprint_from_tools()`. `RepeatedPattern.fingerprint_hash` stores the same value. `SkillCandidate.governance_path` values match `CompiledProposalBundle.review_mode` values.
