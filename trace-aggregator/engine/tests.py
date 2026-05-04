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


# ---------------------------------------------------------------------------
# ENG-05: Determinism corpus
# ---------------------------------------------------------------------------

def test_out_of_order_arrival():
    """DAG structure is identical for all 6 permutations of a 3-span chain."""
    base = [
        Span("a", "",  "A", {"A": 1},                    "llm_call", 10, 10, 100, 1000),
        Span("b", "a", "B", {"A": 1, "B": 1},            "llm_call", 10, 10, 200, 1100),
        Span("c", "b", "C", {"A": 1, "B": 1, "C": 1},   "llm_call", 10, 10, 300, 1300),
    ]
    orderings = [
        [base[0], base[1], base[2]],
        [base[2], base[1], base[0]],
        [base[1], base[0], base[2]],
        [base[2], base[0], base[1]],
        [base[0], base[2], base[1]],
        [base[1], base[2], base[0]],
    ]
    for spans in orderings:
        nodes = reconstruct_dag(spans)
        assert set(nodes["a"].parent_ids) == set()
        assert set(nodes["a"].children) == {"b"}
        assert nodes["a"].parent_resolution == "root"
        assert set(nodes["b"].parent_ids) == {"a"}
        assert set(nodes["b"].children) == {"c"}
        assert nodes["b"].parent_resolution == "explicit"
        assert set(nodes["c"].parent_ids) == {"b"}
        assert set(nodes["c"].children) == set()
        assert nodes["c"].parent_resolution == "explicit"
    print("  ✓ out-of-order arrival (all 6 permutations)")


def test_three_way_fan_in():
    """Three parallel branches all merging into a single collector node."""
    spans = [
        Span("o",   "",   "orch",      {"orch": 1},                                         "llm_call", 0, 0, 10, 1000),
        Span("r1",  "o",  "res1",      {"orch": 1, "res1": 1},                              "llm_call", 0, 0, 10, 1010),
        Span("r2",  "o",  "res2",      {"orch": 1, "res2": 1},                              "llm_call", 0, 0, 10, 1010),
        Span("r3",  "o",  "res3",      {"orch": 1, "res3": 1},                              "llm_call", 0, 0, 10, 1010),
        Span("col", "r1", "collector", {"orch": 1, "res1": 1, "res2": 1, "res3": 1, "col": 1}, "llm_call", 0, 0, 10, 1100),
    ]
    nodes = reconstruct_dag(spans)
    assert set(nodes["o"].children) == {"r1", "r2", "r3"}
    assert nodes["o"].parent_resolution == "root"
    assert set(nodes["col"].parent_ids) == {"r1", "r2", "r3"}
    assert nodes["col"].parent_resolution in {"explicit_plus_fanin", "inferred"}
    assert set(find_roots(nodes)) == {"o"}
    print("  ✓ three-way fan-in")


def test_orphan_parent():
    """Span whose parent_span_id is not in the batch falls back to vector clock."""
    spans = [
        Span("a", "",     "A", {"A": 1},         "llm_call", 0, 0, 10, 1000),
        Span("b", "LOST", "B", {"A": 1, "B": 1}, "llm_call", 0, 0, 10, 1100),
    ]
    nodes = reconstruct_dag(spans)
    assert nodes["b"].inferred_parent is True
    assert "a" in nodes["b"].parent_ids
    assert nodes["b"].parent_resolution == "inferred"
    assert "b" in nodes["a"].children
    print("  ✓ orphan parent falls back to vector clock")


def test_concurrent_spans_are_roots():
    """Two spans with no causal relationship are both independent roots."""
    spans = [
        Span("x", "", "X", {"X": 1}, "llm_call", 0, 0, 10, 1000),
        Span("y", "", "Y", {"Y": 1}, "llm_call", 0, 0, 10, 1005),
    ]
    nodes = reconstruct_dag(spans)
    assert nodes["x"].parent_ids == []
    assert nodes["y"].parent_ids == []
    assert nodes["x"].parent_resolution == "root"
    assert nodes["y"].parent_resolution == "root"
    assert set(find_roots(nodes)) == {"x", "y"}
    print("  ✓ concurrent spans are independent roots")


def test_single_span_trace():
    """A trace with one span is its own root with no children."""
    spans = [Span("only", "", "A", {"A": 1}, "llm_call", 5, 5, 50, 1000)]
    nodes = reconstruct_dag(spans)
    assert len(nodes) == 1
    assert nodes["only"].children == []
    assert nodes["only"].parent_ids == []
    assert nodes["only"].parent_resolution == "root"
    assert find_roots(nodes) == ["only"]
    print("  ✓ single-span trace")


def test_deep_linear_chain():
    """10-hop linear chain: every parent→child link holds and root is correct."""
    n = 10
    spans = []
    for i in range(n):
        clock = {f"agent_{j}": 1 for j in range(i + 1)}
        parent_id = f"s{i - 1}" if i > 0 else ""
        spans.append(Span(f"s{i}", parent_id, f"agent_{i}", clock, "llm_call", 1, 1, 10, 1000 + i * 100))

    nodes = reconstruct_dag(spans)
    for i in range(n - 1):
        assert nodes[f"s{i}"].children == [f"s{i + 1}"], f"s{i} should have exactly child s{i+1}"
    assert nodes[f"s{n - 1}"].children == []
    assert find_roots(nodes) == ["s0"]
    print("  ✓ deep linear chain (10 hops)")


def test_determinism_repeated():
    """reconstruct_dag + serialize_dag produce bit-identical output on 50 repeated calls."""
    spans = [
        Span("o", "",  "orch", {"orch": 1},                         "llm_call", 10, 10,  50, 1000),
        Span("r", "o", "res",  {"orch": 1, "res": 1},               "llm_call", 100, 100, 500, 1100),
        Span("c", "o", "code", {"orch": 1, "code": 1},              "llm_call", 100, 100, 400, 1110),
        Span("v", "r", "rev",  {"orch": 1, "res": 1, "code": 1, "rev": 1},
             "llm_call", 50, 50, 200, 1700),
    ]
    reference = serialize_dag(reconstruct_dag(spans))
    for _ in range(50):
        result = serialize_dag(reconstruct_dag(spans))
        assert result == reference, "reconstruct_dag produced a different result on repeated call"
    print("  ✓ determinism (50 repeated calls, bit-identical)")


def test_fanout_fanin_golden():
    """Golden output for orchestrator → parallel branches → merge pattern."""
    spans = [
        Span("o", "",  "orch", {"orch": 1},                         "llm_call", 10, 10,  50, 1000),
        Span("r", "o", "res",  {"orch": 1, "res": 1},               "llm_call", 100, 100, 500, 1100),
        Span("c", "o", "code", {"orch": 1, "code": 1},              "llm_call", 100, 100, 400, 1110),
        Span("v", "r", "rev",  {"orch": 1, "res": 1, "code": 1, "rev": 1},
             "llm_call", 50, 50, 200, 1700),
    ]
    nodes = reconstruct_dag(spans)

    assert nodes["o"].parent_resolution == "root"
    assert nodes["o"].parent_ids == []
    assert set(nodes["o"].children) == {"r", "c"}

    assert nodes["r"].parent_resolution == "explicit"
    assert nodes["r"].parent_ids == ["o"]
    assert "v" in nodes["r"].children

    assert nodes["c"].parent_resolution == "explicit"
    assert nodes["c"].parent_ids == ["o"]
    assert "v" in nodes["c"].children

    assert "r" in nodes["v"].parent_ids
    assert "c" in nodes["v"].parent_ids
    assert "o" not in nodes["v"].parent_ids
    assert nodes["v"].parent_resolution in {"explicit_plus_fanin", "inferred"}

    dag = serialize_dag(nodes)
    times = [n["start_time_ms"] for n in dag]
    assert times == sorted(times), "serialize_dag should be ordered by start_time_ms"
    print("  ✓ fan-out/fan-in golden output")


def test_blame_error_weight():
    """An agent with errors gets higher blame than one with equal latency/tokens but no errors."""
    spans = [
        Span("a1", "",   "clean", {"clean": 1},          "llm_call", 10, 10, 100, 1000),
        Span("a2", "a1", "clean", {"clean": 2},          "llm_call", 10, 10, 100, 1100),
        Span("b1", "",   "errbug", {"errbug": 1},        "error",    10, 10, 100, 1000),
        Span("b2", "b1", "errbug", {"errbug": 2},        "error",    10, 10, 100, 1100),
        Span("b3", "b2", "errbug", {"errbug": 3},        "error",    10, 10, 100, 1200),
    ]
    blame = compute_blame(spans)
    by_agent = {b.agent_id: b for b in blame}
    assert by_agent["errbug"].blame_score > by_agent["clean"].blame_score
    assert by_agent["errbug"].error_count == 3
    assert by_agent["clean"].error_count == 0
    print("  ✓ blame error weight elevates errbug above clean agent")


def test_blame_single_agent():
    """One agent owning all spans gets blame_score = 80.0 (all latency + all tokens, no errors)."""
    spans = [
        Span("a", "",  "solo", {"solo": 1}, "llm_call", 100, 100, 500, 1000),
        Span("b", "a", "solo", {"solo": 2}, "llm_call", 100, 100, 500, 1500),
    ]
    blame = compute_blame(spans)
    assert len(blame) == 1
    assert blame[0].agent_id == "solo"
    # lat_share=1.0, tok_share=1.0, err_share=0/1=0 → 100*(0.5+0.3+0) = 80.0
    assert blame[0].blame_score == 80.0
    print("  ✓ single-agent blame_score = 80.0")


def test_blame_zero_activity():
    """Spans with zero latency and zero tokens do not raise; scores are all zero."""
    spans = [
        Span("a", "",  "X", {"X": 1}, "llm_call", 0, 0, 0, 1000),
        Span("b", "a", "Y", {"X": 1, "Y": 1}, "llm_call", 0, 0, 0, 1100),
    ]
    blame = compute_blame(spans)
    assert len(blame) == 2
    for b in blame:
        assert b.blame_score == 0.0
    print("  ✓ blame zero-activity edge case (no crash, scores = 0)")


def test_serialize_dag_stable_ordering():
    """serialize_dag is stable when two spans share the same start_time_ms (span_id tiebreak)."""
    spans = [
        Span("aaa", "", "A", {"A": 1},      "llm_call", 0, 0, 10, 1000),
        Span("zzz", "", "Z", {"Z": 1},      "llm_call", 0, 0, 10, 1000),  # same start_time
    ]
    reference = serialize_dag(reconstruct_dag(spans))
    for _ in range(30):
        result = serialize_dag(reconstruct_dag(spans))
        assert result == reference, "serialize_dag ordering is unstable for tied start_time_ms"
    print("  ✓ serialize_dag stable ordering with tied start_time_ms")


def main():
    print("Running engine tests:")
    # Original smoke tests
    test_precedes()
    test_linear_chain()
    test_parallel_fanout()
    test_inferred_parent_when_lost()
    test_blame_ranking()
    test_serialize_dag()
    test_decision_proto_roundtrip()
    test_decision_descendant_linkage()
    # ENG-05: Determinism corpus
    print("\nDeterminism corpus:")
    test_out_of_order_arrival()
    test_three_way_fan_in()
    test_orphan_parent()
    test_concurrent_spans_are_roots()
    test_single_span_trace()
    test_deep_linear_chain()
    test_determinism_repeated()
    test_fanout_fanin_golden()
    test_blame_error_weight()
    test_blame_single_agent()
    test_blame_zero_activity()
    test_serialize_dag_stable_ordering()
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
