"""koretex-agent kernel: one runtime, many profiles.

A profile = system prompt + tool subset + model tier + hard prefix budget.
Sessions are bounded; long-horizon state lives in the mission coordinator;
every session is recorded as a (contract, trajectory, verdict) triple.
"""
__version__ = "0.1.0"
