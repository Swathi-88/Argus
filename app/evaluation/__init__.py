"""
Phase 4 evaluation harness.

Generates a labeled synthetic scenario where the ground truth is known by
construction, then runs two policies over the identical event stream:

* **baseline** — static periodic review on a fixed schedule (the incumbent),
* **engine**   — event-driven continuous reassessment (this system),

and reports detection recall, false positive rate, detection latency, and
entity-resolution precision/recall for both.
"""
from app.evaluation.scenario import ScenarioConfig, generate_scenario
from app.evaluation.runner import run_evaluation

__all__ = ["ScenarioConfig", "generate_scenario", "run_evaluation"]
