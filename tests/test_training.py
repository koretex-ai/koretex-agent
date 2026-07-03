"""Loop-3 trajectory harvest: labeling, gate-linking, SFT/DPO builders."""
import json

from koretex_agent import training as tr
from koretex_agent.training import SessionRecord, build_dpo, build_sft, harvest


def _traj(path, profile, order_id, task, verdict, turns=("run_shell",)):
    """Write a minimal trajectory jsonl (start · messages · usage · verdict)."""
    lines = [{"event": "start", "profile": profile, "contract": {"order_id": order_id, "task": task}}]
    lines.append({"event": "message", "msg": {"role": "system", "content": "sys"}})
    lines.append({"event": "message", "msg": {"role": "user", "content": f"WORK ORDER {order_id}\nTask: {task}"}})
    for t in turns:
        lines.append({"event": "message", "msg": {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": t, "arguments": "{}"}}]}})
        lines.append({"event": "usage", "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
    lines.append({"event": "verdict", "verdict": verdict})
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))


def _worker_verdict(done, attention=False):
    return {"order_id": "o", "done": done, "report": "r", "request_attention": attention}


def test_load_and_self_report_labels(tmp_path):
    _traj(tmp_path / "a.jsonl", "worker", "m-11111111-aaaaaa", "task A", _worker_verdict(True))
    _traj(tmp_path / "b.jsonl", "worker", "m-11111111-bbbbbb", "task B", _worker_verdict(False, attention=True))
    sessions = tr.load_sessions(tmp_path)
    labels = {s.task: s.label for s in sessions}
    assert labels == {"task A": "pass", "task B": "fail"}
    assert all(s.mission_id == "m-11111111" for s in sessions)  # parsed from order_id


def test_gate_labels_override_self_report(tmp_path):
    # worker self-reports done=true, but the gate says the task failed
    _traj(tmp_path / "a.jsonl", "worker", "m-22222222-aaaaaa", "task X", _worker_verdict(True))
    sessions = tr.load_sessions(tmp_path)
    assert sessions[0].label == "pass"
    tr.apply_gate_labels(sessions, {("m-22222222", "task X"): False})
    assert sessions[0].label == "fail"  # authoritative gate wins


def test_build_sft_only_passing_workers(tmp_path):
    _traj(tmp_path / "a.jsonl", "worker", "m-3-aaaaaa", "task A", _worker_verdict(True))
    _traj(tmp_path / "b.jsonl", "worker", "m-3-bbbbbb", "task B", _worker_verdict(False))
    _traj(tmp_path / "c.jsonl", "validator", "m-3-cccccc", "task A", {"order_id": "o", "items": [], "overall_passed": True})
    sessions = tr.load_sessions(tmp_path)
    sft = build_sft(sessions)
    assert len(sft) == 1 and sft[0]["task"] == "task A"      # only the passing worker
    assert sft[0]["messages"][0]["role"] == "system"


def test_build_dpo_pairs_pass_and_fail_same_task(tmp_path):
    _traj(tmp_path / "pass.jsonl", "worker", "m-4-aaaaaa", "same task", _worker_verdict(True), turns=("write_file", "run_shell"))
    _traj(tmp_path / "fail.jsonl", "worker", "m-4-bbbbbb", "same task", _worker_verdict(False), turns=("run_shell",))
    sessions = tr.load_sessions(tmp_path)
    dpo = build_dpo(sessions)
    assert len(dpo) == 1
    ex = dpo[0]
    assert ex["task"] == "same task"
    assert len(ex["prompt"]) == 2                 # system + work order
    assert ex["chosen"] and ex["rejected"]        # both trajectories present
    assert ex["chosen"] != ex["rejected"]


def test_harvest_stats_and_write(tmp_path):
    _traj(tmp_path / "a.jsonl", "worker", "m-5-aaaaaa", "t", _worker_verdict(True))
    out = harvest(store=tmp_path, routing_store=tmp_path / "no_routing")
    assert out["stats"]["worker_pass"] == 1 and out["stats"]["counts"]["worker_sft"] == 1
    paths = tr.write_datasets(tmp_path / "ds", out)
    assert (tmp_path / "ds" / "worker_sft.jsonl").exists()
    assert json.loads((tmp_path / "ds" / "stats.json").read_text())["counts"]["worker_sft"] == 1


def _val_traj(path, profile, order_id, task, overall_passed, clean=True):
    """A validator/scrutiny trajectory; `clean` = terminated without a tool call."""
    lines = [
        {"event": "start", "profile": profile, "contract": {"order_id": order_id, "task": task}},
        {"event": "message", "msg": {"role": "system", "content": "judge"}},
        {"event": "message", "msg": {"role": "user", "content": f"validate {task}"}},
    ]
    last = {"role": "assistant", "content": "verdict"}
    if not clean:
        last["tool_calls"] = [{"id": "1", "function": {"name": "run_shell", "arguments": "{}"}}]
    lines.append({"event": "message", "msg": last})
    lines.append({"event": "verdict", "verdict": {"order_id": order_id, "items": [], "overall_passed": overall_passed}})
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))


def test_validator_sft_keeps_correct_final_verdicts(tmp_path):
    # task cleared → a lane that passed it (cleanly) is a correct positive
    _val_traj(tmp_path / "v.jsonl", "validator", "m-9-aaaaaa", "task Z", overall_passed=True)
    _val_traj(tmp_path / "s.jsonl", "scrutiny", "m-9-bbbbbb", "task Z", overall_passed=False)  # wrong
    sessions = tr.load_sessions(tmp_path)
    sft, dissent = tr.build_validator_sft(sessions, {("m-9", "task Z"): True})
    profiles = {e["profile"] for e in sft}
    assert profiles == {"validator"}          # only the correct lane kept
    assert dissent == 1                        # the lanes disagreed → one was wrong


def test_validator_sft_drops_cut_off_verdicts(tmp_path):
    # correct verdict but the session hit the turn cap (ended on a tool call) → drop
    _val_traj(tmp_path / "v.jsonl", "validator", "m-9-aaaaaa", "task Z", overall_passed=True, clean=False)
    sessions = tr.load_sessions(tmp_path)
    sft, _ = tr.build_validator_sft(sessions, {("m-9", "task Z"): True})
    assert sft == []


def test_routing_sft_and_escalation_dpo():
    entries = [
        {"message": "add a flag", "route": "task", "worker_done": True, "work": "add a flag"},
        {"message": "build a whole app", "route": "task->mission", "mission_status": "done", "work": "build a whole app"},
        {"message": "what is 2+2", "route": "chat"},  # unverifiable → no training example
    ]
    sft, dpo = tr.build_routing(entries)
    decided = {e["message"]: json.loads(e["messages"][-1]["content"])["decision"] for e in sft}
    assert decided == {"add a flag": "task", "build a whole app": "mission"}  # escalation corrected to mission
    assert len(dpo) == 1
    assert json.loads(dpo[0]["chosen"][0]["content"])["decision"] == "mission"
    assert json.loads(dpo[0]["rejected"][0]["content"])["decision"] == "task"
