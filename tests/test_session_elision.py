"""Wire-level tool-result elision — the efficiency fix for O(turns^2) prompt cost."""
from koretex_agent.session import _elide_old_tool_results


def _msgs(n_tools):
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "order"}]
    for i in range(n_tools):
        m.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}"}]})
        m.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"OUTPUT-{i} " + "x" * 100})
    return m


def test_no_elision_below_threshold():
    m = _msgs(2)  # 2 tool results, keep_last=3 → untouched
    assert _elide_old_tool_results(m) is m


def test_elides_all_but_recent():
    m = _msgs(5)  # 5 tool results, keep last 3 full
    out = _elide_old_tool_results(m, keep_last=3)
    tools = [x for x in out if x["role"] == "tool"]
    assert tools[0]["content"].startswith("[earlier tool output elided")
    assert tools[1]["content"].startswith("[earlier tool output elided")
    assert tools[2]["content"].startswith("OUTPUT-2")   # recent kept verbatim
    assert tools[3]["content"].startswith("OUTPUT-3")
    assert tools[4]["content"].startswith("OUTPUT-4")


def test_does_not_mutate_original():
    m = _msgs(5)
    before = m[3]["content"]
    _elide_old_tool_results(m, keep_last=3)
    assert m[3]["content"] == before  # original history untouched (trajectory keeps full)


def test_preserves_non_tool_messages_and_order():
    m = _msgs(5)
    out = _elide_old_tool_results(m, keep_last=1)
    assert out[0]["role"] == "system" and out[0]["content"] == "sys"
    assert out[1]["role"] == "user" and out[1]["content"] == "order"
    # assistant tool_calls preserved so the model still sees *what* it ran
    assert [x["role"] for x in out] == [x["role"] for x in m]
    assert all("tool_calls" in x for x in out if x["role"] == "assistant")


def test_elided_placeholder_reports_size():
    m = _msgs(5)
    out = _elide_old_tool_results(m, keep_last=1)
    stale = next(x for x in out if x["role"] == "tool" and "elided" in x["content"])
    assert "chars" in stale["content"]
