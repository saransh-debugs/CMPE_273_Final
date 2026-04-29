"""
Pure-Python smoke tests for the Causal Engine. No ClickHouse / gRPC needed.

Run:
    python -m engine.tests
"""
from .dag import Span, reconstruct_dag, find_roots, serialize_dag, _precedes
from .blame import compute_blame
from .worker import _collect_descendants
from generated import tracing_pb2


def test_precedes():
    assert _precedes({"a": 1}, {"a": 2})
    assert _precedes({"a": 1}, {"a": 1, "b": 1})
    assert not _precedes({"a": 1}, {"a": 1})              # equal
    assert not _precedes({"a": 2}, {"a": 1})              # reverse
    assert not _precedes({"a": 1, "b": 1}, {"a": 0, "b": 2})  # concurrent
    print("  ✓ _precedes")


def test_linear_chain():
    """A -> B -> C with vector clocks {A:1}, {A:1,B:1}, {A:1,B:1,C:1}."""
    spans = [
        Span("a", "", "A", {"A": 1}, "llm_call", 10, 10, 100, 1000),
        Span("b", "a", "B", {"A": 1, "B": 1}, "llm_call", 10, 10, 200, 1100),
        Span("c", "b", "C", {"A": 1, "B": 1, "C": 1}, "llm_call", 10, 10, 300, 1300),
    ]
    nodes = reconstruct_dag(spans)
    assert nodes["a"].children == ["b"]
    assert nodes["b"].children == ["c"]
    assert nodes["c"].children == []
    assert find_roots(nodes) == ["a"]
    print("  ✓ linear chain")


def test_parallel_fanout():
    """Orchestrator fans out to research + coder in parallel."""
    spans = [
        Span("o", "", "orch", {"orch": 1}, "llm_call", 10, 10, 50, 1000),
        Span("r", "o", "res", {"orch": 1, "res": 1}, "llm_call", 100, 100, 500, 1100),
        Span("c", "o", "code", {"orch": 1, "code": 1}, "llm_call", 100, 100, 400, 1110),
        # Reviewer sees both upstream clocks merged.
        Span("v", "r", "rev", {"orch": 1, "res": 1, "code": 1, "rev": 1},
             "llm_call", 50, 50, 200, 1700),
    ]
    nodes = reconstruct_dag(spans)
    assert set(nodes["o"].children) == {"r", "c"}
    assert "v" in nodes["r"].children and "v" in nodes["c"].children
    assert set(nodes["v"].parent_ids) == {"r", "c"}
    assert nodes["v"].parent_resolution in {"explicit_plus_fanin", "inferred"}
    print("  ✓ parallel fanout")


def test_inferred_parent_when_lost():
    """If parent_span_id is missing, vector clocks should infer it."""
    spans = [
        Span("a", "", "A", {"A": 1}, "llm_call", 10, 10, 100, 1000),
        Span("b", "", "B", {"A": 1, "B": 1}, "llm_call", 10, 10, 200, 1100),  # parent lost
    ]
    nodes = reconstruct_dag(spans)
    assert nodes["a"].children == ["b"]
    assert nodes["b"].inferred_parent is True
    print("  ✓ inferred parent via vector clock")


def test_blame_ranking():
    spans = [
        Span("a", "", "fast", {"fast": 1}, "llm_call", 10, 10, 50, 1000),
        Span("b", "a", "slow", {"fast": 1, "slow": 1}, "llm_call", 500, 500, 5000, 1050),
        Span("c", "b", "fast", {"fast": 2, "slow": 1}, "llm_call", 10, 10, 50, 6050),
    ]
    blame = compute_blame(spans)
    assert blame[0].agent_id == "slow", "Slow agent should be the most blamed"
    assert blame[0].blame_score > blame[1].blame_score
    print("  ✓ blame ranking")


def test_serialize_dag():
    spans = [
        Span("a", "", "A", {"A": 1}, "llm_call", 10, 10, 100, 1000),
        Span("b", "a", "B", {"A": 1, "B": 1}, "llm_call", 10, 10, 200, 1100),
    ]
    out = serialize_dag(reconstruct_dag(spans))
    assert len(out) == 2
    assert out[0]["start_time_ms"] <= out[1]["start_time_ms"]
    assert "parent_span_ids" in out[0]
    assert "parent_resolution" in out[0]
    print("  ✓ serialize_dag")


def test_decision_proto_roundtrip():
    d = tracing_pb2.DecisionEvent(
        trace_id="t1",
        decision_id="d1",
        source_span_id="s1",
        actor_agent_id="orchestrator",
        decision_type="agent_handoff",
        selected_candidate_id="research_agent",
        confidence=0.81,
        rationale_summary="Need domain research first",
        evidence_refs=["task=market_scan"],
        candidates=[
            tracing_pb2.DecisionCandidate(
                candidate_id="research_agent",
                candidate_type="agent",
                score=0.81,
                reason="best fit",
            )
        ],
        timestamp_ms=123,
    )
    decoded = tracing_pb2.DecisionEvent()
    decoded.ParseFromString(d.SerializeToString())
    assert decoded.decision_id == "d1"
    assert decoded.candidates[0].candidate_id == "research_agent"
    print("  ✓ decision proto roundtrip")


def test_decision_descendant_linkage():
    spans = [
        Span("o", "", "orch", {"orch": 1}, "llm_call", 0, 0, 10, 1000),
        Span("r", "o", "res", {"orch": 1, "res": 1}, "llm_call", 0, 0, 10, 1001),
        Span("c", "o", "code", {"orch": 1, "code": 1}, "llm_call", 0, 0, 10, 1001),
        Span("v", "r", "rev", {"orch": 1, "res": 1, "code": 1, "rev": 1}, "llm_call", 0, 0, 10, 1002),
    ]
    nodes = reconstruct_dag(spans)
    targets = set(_collect_descendants(nodes, "o"))
    assert {"r", "c", "v"}.issubset(targets)
    print("  ✓ decision descendant linkage")


def main():
    print("Running engine tests:")
    test_precedes()
    test_linear_chain()
    test_parallel_fanout()
    test_inferred_parent_when_lost()
    test_blame_ranking()
    test_serialize_dag()
    test_decision_proto_roundtrip()
    test_decision_descendant_linkage()
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
