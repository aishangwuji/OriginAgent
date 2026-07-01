from OriginAgent.agent.skill_bootstrapper_models import (
    ActionTraceDigest,
    RepeatedPattern,
    SkillCandidate,
)


class TestActionTraceDigest:
    def test_minimal(self):
        d = ActionTraceDigest(digest_id="d1", session_key="s1", tool_sequence=["read_file", "grep"])
        assert d.digest_id == "d1"
        assert d.tool_sequence == ["read_file", "grep"]
        assert d.fingerprint != ""

    def test_empty_sequence(self):
        d = ActionTraceDigest(digest_id="d2", session_key="s2", tool_sequence=[])
        assert d.fingerprint == ""  # empty sequence => empty fingerprint


class TestRepeatedPattern:
    def test_minimal(self):
        rp = RepeatedPattern(
            pattern_id="rp1",
            fingerprint_hash="abc123",
            tool_signature="read_file+grep",
            repeat_count=3,
            session_keys=["s1", "s2", "s3"],
        )
        assert rp.repeat_count == 3
        assert rp.confidence == 0.0

    def test_confidence(self):
        rp = RepeatedPattern(
            pattern_id="rp2",
            fingerprint_hash="def456",
            tool_signature="web_search+web_fetch",
            repeat_count=5,
            session_keys=["s1"] * 5,
            confidence=0.85,
        )
        assert rp.confidence == 0.85


class TestSkillCandidate:
    def test_minimal(self):
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

    def test_defaults(self):
        sc = SkillCandidate(
            candidate_id="sc2", pattern_id="rp2", skill_name="test-skill",
            description="test", body="test",
        )
        assert not sc.dangerous_tools
        assert sc.verification_plan == []


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
