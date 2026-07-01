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

from OriginAgent.agent.skill_bootstrapper import SkillBootstrapperScanner
from OriginAgent.agent.skill_bootstrapper_models import ActionTraceDigest


class TestScanner:
    def test_empty(self):
        scanner = SkillBootstrapperScanner()
        patterns = scanner.scan([])
        assert patterns == []

    def test_single_digest_no_repeat(self):
        scanner = SkillBootstrapperScanner()
        digests = [ActionTraceDigest(digest_id="d1", session_key="s1", tool_sequence=["read_file"])]
        patterns = scanner.scan(digests, min_repeats=2)
        assert len(patterns) == 0

    def test_two_matching(self):
        scanner = SkillBootstrapperScanner()
        digests = [
            ActionTraceDigest(digest_id="d1", session_key="s1", tool_sequence=["read_file", "grep"]),
            ActionTraceDigest(digest_id="d2", session_key="s2", tool_sequence=["read_file", "grep"]),
        ]
        patterns = scanner.scan(digests, min_repeats=2)
        assert len(patterns) == 1
        assert patterns[0].repeat_count == 2

    def test_three_sessions(self):
        scanner = SkillBootstrapperScanner()
        digests = [
            ActionTraceDigest(digest_id=f"d{i}", session_key=f"s{i}", tool_sequence=["web_search"])
            for i in range(3)
        ]
        patterns = scanner.scan(digests, min_repeats=2)
        assert len(patterns) == 1
        assert patterns[0].repeat_count == 3

    def test_two_distinct_patterns(self):
        scanner = SkillBootstrapperScanner()
        digests = [
            ActionTraceDigest(digest_id="d1", session_key="s1", tool_sequence=["read_file"]),
            ActionTraceDigest(digest_id="d2", session_key="s2", tool_sequence=["read_file"]),
            ActionTraceDigest(digest_id="d3", session_key="s3", tool_sequence=["grep"]),
            ActionTraceDigest(digest_id="d4", session_key="s4", tool_sequence=["grep"]),
        ]
        patterns = scanner.scan(digests, min_repeats=2)
        assert len(patterns) == 2

    def test_respects_min_repeats(self):
        scanner = SkillBootstrapperScanner()
        digests = [
            ActionTraceDigest(digest_id=f"d{i}", session_key=f"s{i}", tool_sequence=["read_file"])
            for i in range(3)
        ]
        assert len(scanner.scan(digests, min_repeats=1)) == 1
        assert len(scanner.scan(digests, min_repeats=2)) == 1
        assert len(scanner.scan(digests, min_repeats=4)) == 0

    def test_dedupe_fingerprint(self):
        scanner = SkillBootstrapperScanner()
        digests = [
            ActionTraceDigest(digest_id=f"d{i}", session_key=f"s{i}", tool_sequence=["read_file"])
            for i in range(3)
        ]
        patterns = scanner.scan(digests, min_repeats=2)
        assert patterns[0].fingerprint_hash == digests[0].fingerprint

from OriginAgent.agent.skill_bootstrapper import SkillCandidateCompiler, compile_to_proposal_bundle
from OriginAgent.agent.skill_bootstrapper_models import RepeatedPattern, SkillCandidate
from OriginAgent.agent.meta_programming import CompiledProposalBundle


class TestCompiler:
    def test_minimal(self):
        compiler = SkillCandidateCompiler()
        pattern = RepeatedPattern(
            pattern_id="rp1", fingerprint_hash="abc", tool_signature="read_file+grep",
            repeat_count=3, session_keys=["s1", "s2", "s3"], confidence=0.7,
        )
        candidate = compiler.compile(pattern)
        assert candidate is not None
        assert candidate.pattern_id == "rp1"
        assert candidate.confidence == 0.7

    def test_skill_name_format(self):
        compiler = SkillCandidateCompiler()
        pattern = RepeatedPattern(
            pattern_id="rp2", fingerprint_hash="def", tool_signature="web_search+web_fetch",
            repeat_count=5, session_keys=["s1"] * 5, confidence=0.85,
        )
        candidate = compiler.compile(pattern)
        assert candidate.skill_name == "web-search-web-fetch"
        assert candidate.governance_path == "review_required"

    def test_dangerous_tools_detected(self):
        compiler = SkillCandidateCompiler()
        pattern = RepeatedPattern(
            pattern_id="rp3", fingerprint_hash="ghi", tool_signature="exec",
            repeat_count=3, session_keys=["s1", "s2", "s3"], confidence=0.7,
        )
        candidate = compiler.compile(pattern)
        assert candidate is not None
        assert "exec" in candidate.dangerous_tools

    def test_low_confidence_returns_none(self):
        compiler = SkillCandidateCompiler()
        pattern = RepeatedPattern(
            pattern_id="rp4", fingerprint_hash="jkl", tool_signature="read_file",
            repeat_count=2, session_keys=["s1", "s2"], confidence=0.3,
        )
        candidate = compiler.compile(pattern, min_confidence=0.5)
        assert candidate is None

    def test_compile_to_proposal_bundle(self):
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

from OriginAgent.agent.skill_bootstrapper import (
    SkillBootstrapperScanner,
    SkillCandidateCompiler,
    compile_to_proposal_bundle,
)
from OriginAgent.agent.skill_bootstrapper_models import ActionTraceDigest
from OriginAgent.agent.meta_programming import CompiledProposalBundle


class TestEndToEnd:
    def test_basic_pipeline(self):
        scanner = SkillBootstrapperScanner()
        compiler = SkillCandidateCompiler()
        digests = [
            ActionTraceDigest(digest_id=f"d{i}", session_key=f"s{i % 3}",
                              tool_sequence=["read_file", "grep"])
            for i in range(5)
        ]
        digests.append(ActionTraceDigest(digest_id="d5", session_key="s3",
                                         tool_sequence=["web_search"]))
        patterns = scanner.scan(digests, min_repeats=2)
        assert len(patterns) == 1
        assert patterns[0].tool_signature == "read_file+grep"
        assert patterns[0].repeat_count == 5

        candidate = compiler.compile(patterns[0])
        assert candidate is not None
        assert candidate.skill_name == "read-file-grep"
        assert candidate.confidence >= 0.5

        bundle = compile_to_proposal_bundle(candidate)
        assert isinstance(bundle, CompiledProposalBundle)
        assert bundle.target_type == "skill"
        assert bundle.target_key == "read-file-grep"
        assert bundle.review_mode == "review_required"

    def test_dangerous_tools_pipeline(self):
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

    def test_below_threshold(self):
        scanner = SkillBootstrapperScanner()
        digests = [
            ActionTraceDigest(digest_id="d1", session_key="s1", tool_sequence=["read_file"]),
            ActionTraceDigest(digest_id="d2", session_key="s2", tool_sequence=["grep"]),
        ]
        patterns = scanner.scan(digests, min_repeats=3)
        assert len(patterns) == 0
