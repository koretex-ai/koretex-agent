"""The anti-drift gate. Hermes reached a 25K-token fixed prefix one good
intention at a time; this test makes that drift a build failure instead.
Raising a budget number is allowed — but it must happen in a reviewed diff,
never silently."""
from koretex_agent.budget import profile_prefix_tokens
from koretex_agent.profiles import ALL


def test_every_profile_within_prefix_budget():
    report = {}
    for name, profile in ALL.items():
        used = profile_prefix_tokens(profile)
        report[name] = (used, profile.prefix_budget_tokens)
        assert used <= profile.prefix_budget_tokens, (
            f"profile '{name}' prefix is {used} tokens, over its "
            f"{profile.prefix_budget_tokens}-token budget. Cut the prompt or "
            f"schemas — do not raise the budget without a reviewed decision."
        )
    print("prefix usage:", {k: f"{u}/{b}" for k, (u, b) in report.items()})
