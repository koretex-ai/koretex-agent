"""Wire-level stale-context elision — the efficiency fix for O(turns^2) prompt
cost. Elides BOTH stale tool results (reader/validator cost) and stale assistant
tool_call arguments, chiefly write_file content (writer/worker cost)."""
import json

from koretex_agent.session import _elide_stale_context

BIG = "x" * 500  # > _ELIDE_MIN (200) → elision-eligible


def _turns(n):
    """A system+order prefix then n turns of: assistant writes a big file, tool
    confirms with a big result."""
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "order"}]
    for i in range(n):
        m.append({"role": "assistant", "content": "",
                  "tool_calls": [{"id": f"c{i}", "function": {
                      "name": "write_file",
                      "arguments": json.dumps({"path": f"f{i}.py", "content": BIG})}}]})
        m.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"RESULT-{i} " + BIG})
    return m


def test_no_elision_below_threshold():
    m = _turns(2)  # 2 turns, keep_last=3 → untouched
    assert _elide_stale_context(m) is m


def test_elides_stale_tool_results():
    out = _elide_stale_context(_turns(5), keep_last=3)
    tools = [x for x in out if x["role"] == "tool"]
    assert tools[0]["content"].startswith("[earlier tool output elided")
    assert tools[1]["content"].startswith("[earlier tool output elided")
    assert tools[-1]["content"].startswith("RESULT-4")  # recent kept verbatim


def test_elides_stale_write_file_args():
    # the worker gap: big content in assistant tool_call args, re-sent every turn
    out = _elide_stale_context(_turns(5), keep_last=3)
    asst = [x for x in out if x["role"] == "assistant"]
    stale_args = json.loads(asst[0]["tool_calls"][0]["function"]["arguments"])
    assert "_elided" in stale_args and "content" not in stale_args
    # function name is preserved so the model still sees *what* it did
    assert asst[0]["tool_calls"][0]["function"]["name"] == "write_file"
    recent_args = json.loads(asst[-1]["tool_calls"][0]["function"]["arguments"])
    assert recent_args.get("content") == BIG  # last keep_last turns kept full


def test_keeps_last_n_turns_full():
    out = _elide_stale_context(_turns(6), keep_last=2)
    asst = [x for x in out if x["role"] == "assistant"]
    kept = [a for a in asst if "content" in json.loads(a["tool_calls"][0]["function"]["arguments"])]
    assert len(kept) == 2


def test_small_args_and_results_untouched():
    m = [{"role": "system", "content": "s"}, {"role": "user", "content": "o"}]
    for i in range(5):
        m.append({"role": "assistant", "content": "",
                  "tool_calls": [{"id": f"c{i}", "function": {"name": "run_shell",
                      "arguments": json.dumps({"command": "ls"})}}]})  # tiny
        m.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})  # tiny
    out = _elide_stale_context(m, keep_last=1)
    assert all("elided" not in (x.get("content") or "") for x in out if x["role"] == "tool")
    assert all("_elided" not in x["tool_calls"][0]["function"]["arguments"]
               for x in out if x["role"] == "assistant")


def test_does_not_mutate_original():
    m = _turns(5)
    before = m[-1]["content"]
    _elide_stale_context(m, keep_last=3)
    assert m[-1]["content"] == before
    assert "content" in json.loads(m[2]["tool_calls"][0]["function"]["arguments"])  # first asst intact


def test_preserves_order_and_roles():
    m = _turns(5)
    out = _elide_stale_context(m, keep_last=2)
    assert [x["role"] for x in out] == [x["role"] for x in m]
    assert out[0]["content"] == "sys" and out[1]["content"] == "order"
