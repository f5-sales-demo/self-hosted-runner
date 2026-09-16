from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "reconcile_arc_listeners", ROOT / "scripts/reconcile-arc-listeners.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def listener(name: str, runner_namespace: str, ephemeral_set: str) -> dict:
    return {
        "kind": "AutoscalingListener",
        "metadata": {"name": name, "namespace": "arc-systems", "uid": f"uid-{name}"},
        "spec": {
            "autoscalingRunnerSetNamespace": runner_namespace,
            "ephemeralRunnerSetName": ephemeral_set,
        },
    }


def ephemeral_set(name: str, namespace: str) -> dict:
    return {
        "kind": "EphemeralRunnerSet",
        "metadata": {"name": name, "namespace": namespace},
    }


def pod(
    name: str, *, phase: str = "Running", ready: bool = True, restarts: int = 0
) -> dict:
    return {
        "metadata": {"name": name},
        "status": {
            "phase": phase,
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "containerStatuses": [{"restartCount": restarts}],
        },
    }


class ReconcileArcListenersTests(unittest.TestCase):
    def test_stale_detection_is_scoped_to_requested_namespaces(self) -> None:
        resources = {
            "items": [
                listener("healthy", "runner-a", "set-a"),
                listener("stale", "runner-b", "set-old"),
                listener("unrelated", "runner-c", "missing"),
                ephemeral_set("set-a", "runner-a"),
                ephemeral_set("set-new", "runner-b"),
            ]
        }
        self.assertEqual(
            ["stale"],
            [
                item["name"]
                for item in MODULE.stale_listeners(resources, {"runner-a", "runner-b"})
            ],
        )

    def test_listener_health_requires_one_ready_zero_restart_pod_per_namespace(
        self,
    ) -> None:
        resources = {
            "items": [
                listener("listener-a", "runner-a", "set-a"),
                listener("listener-b", "runner-b", "set-b"),
                ephemeral_set("set-a", "runner-a"),
                ephemeral_set("set-b", "runner-b"),
            ]
        }
        pods = {"items": [pod("listener-a"), pod("listener-b")]}
        self.assertEqual(
            {"missing": [], "duplicate": [], "stale": [], "unhealthy": []},
            MODULE.listener_health(resources, pods, {"runner-a", "runner-b"}),
        )

        pods["items"][1] = pod("listener-b", ready=False, restarts=1)
        health = MODULE.listener_health(resources, pods, {"runner-a", "runner-b"})
        self.assertEqual([], health["missing"])
        self.assertEqual([], health["stale"])
        self.assertEqual(
            ["listener-b:phase=Running,ready=False,restarts=1"], health["unhealthy"]
        )

    def test_listener_health_reports_missing_duplicate_and_stale_references(
        self,
    ) -> None:
        resources = {
            "items": [
                listener("listener-a", "runner-a", "old-set"),
                listener("listener-a-duplicate", "runner-a", "set-a"),
                ephemeral_set("set-a", "runner-a"),
            ]
        }
        health = MODULE.listener_health(
            resources,
            {"items": [pod("listener-a-duplicate")]},
            {"runner-a", "runner-b"},
        )
        self.assertEqual(["runner-b"], health["missing"])
        self.assertEqual(["runner-a"], health["duplicate"])
        self.assertEqual(["arc-systems/listener-a"], health["stale"])
        self.assertEqual(["listener-a:missing-pod"], health["unhealthy"])


if __name__ == "__main__":
    unittest.main()
