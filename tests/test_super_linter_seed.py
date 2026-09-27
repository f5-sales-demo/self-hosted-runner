from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ACTION_COMMIT = "4ce20838b8ab83717e78138c5b3a1407148e0918"
INDEX_DIGEST = "sha256:c05768164eed53bac7c82aade7a14a76955206d4962cd41be97118db96fa5996"
MANIFEST_DIGEST = (
    "sha256:a38987de6efa8b7286ef98233eb8454cd1370ab58eeab8190ddd74fe0c7ca849"
)
SEED_DIGEST = "sha256:" + "d" * 64
IMAGE_ID = "sha256:" + "b" * 64


class SuperLinterSeedTests(unittest.TestCase):
    def test_seed_profile_is_built_published_and_attested(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        publish = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        builder = (ROOT / "scripts/build-super-linter-seed.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("FROM ${DOCKER_CLI_IMAGE} AS super-linter-seed", dockerfile)
        self.assertIn(".super-linter-seed/super-linter.tar.zst", dockerfile)
        self.assertIn('f5.sales-demo.runner.profile="super-linter-seed"', dockerfile)
        self.assertIn("profile: super-linter-seed", publish)
        self.assertIn("target: super-linter-seed", publish)
        self.assertIn("scripts/build-super-linter-seed.sh", publish)
        self.assertIn("actions/attest-build-provenance@", publish)
        for immutable in (ACTION_COMMIT, INDEX_DIGEST, MANIFEST_DIGEST):
            self.assertIn(immutable, builder)
        for command in (
            "docker buildx imagetools inspect",
            "docker image pull --platform linux/amd64",
            "docker image save",
            "zstd --threads=0",
            "sha256sum",
        ):
            self.assertIn(command, builder)

    def test_deployment_requires_and_verifies_immutable_seed(self) -> None:
        deploy = (ROOT / "scripts/arc-deploy.sh").read_text(encoding="utf-8")
        for fragment in (
            "SUPER_LINTER_SEED_IMAGE must be an immutable approved registry reference",
            "SUPER_LINTER_SEED_SOURCE_IMAGE must identify the equal GHCR digest",
            'scripts/mirror-runner-image.sh verify "$SUPER_LINTER_SEED_SOURCE_IMAGE" "$SUPER_LINTER_SEED_IMAGE"',
            '--set-string "seedImage=$SUPER_LINTER_SEED_IMAGE"',
            "SUPER_LINTER_SEED_IMAGE_REQUIRED",
        ):
            self.assertIn(fragment, deploy)
        schema = json.loads((ROOT / "arc/prepull/values.schema.json").read_text())
        self.assertIn("seedImage", schema["required"])
        self.assertRegex(
            "ghcr.io/f5-sales-demo/self-hosted-runner@" + SEED_DIGEST,
            schema["properties"]["seedImage"]["pattern"],
        )
        template = (ROOT / "arc/prepull/templates/daemonset.yaml").read_text()
        self.assertIn("name: pull-super-linter-seed", template)
        self.assertIn("image: {{ .Values.seedImage | quote }}", template)

    def test_runner_loads_after_dind_without_sharing_docker_storage(self) -> None:
        values = yaml.safe_load((ROOT / "arc/container-build-values.yaml").read_text())
        pod = values["template"]["spec"]
        names = [container["name"] for container in pod["initContainers"]]
        self.assertEqual(
            [
                "init-runner-runtime",
                "init-dind-externals",
                "dind",
                "load-super-linter-seed",
            ],
            names,
        )
        loader = pod["initContainers"][-1]
        dind = pod["initContainers"][-2]
        self.assertEqual(["/bin/sh", "-c"], dind["command"])
        for fragment in (
            "/sys/fs/cgroup/cpu.stat",
            "/sys/fs/cgroup/memory.current",
            "/sys/fs/cgroup/memory.peak",
            "/sys/fs/cgroup/memory.events",
            "/sys/fs/cgroup/io.stat",
            "/var/lib/docker",
            "dind-sidecar.json",
        ):
            self.assertIn(fragment, dind["args"][0])
        syntax = subprocess.run(
            ["sh", "-n"],
            input=dind["args"][0],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, syntax.returncode, syntax.stderr)
        self.assertIn(
            {"name": "runner-runtime", "mountPath": "/runner-runtime"},
            dind["volumeMounts"],
        )
        self.assertEqual("SUPER_LINTER_SEED_IMAGE_REQUIRED", loader["image"])
        self.assertTrue(loader["securityContext"]["runAsNonRoot"])
        self.assertTrue(loader["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(loader["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(["ALL"], loader["securityContext"]["capabilities"]["drop"])
        mounts = {mount["name"]: mount for mount in loader["volumeMounts"]}
        self.assertEqual("/var/run", mounts["dind-sock"]["mountPath"])
        self.assertEqual("/runner-runtime", mounts["runner-runtime"]["mountPath"])
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        self.assertEqual({"sizeLimit": "100Gi"}, volumes["dind-storage"]["emptyDir"])
        self.assertNotIn("persistentVolumeClaim", str(volumes))
        self.assertNotIn("hostPath", str(volumes))

        validator = (ROOT / "scripts/validate-arc.sh").read_text(encoding="utf-8")
        self.assertIn("grep -n -m1 -- 'name: dind$'", validator)
        self.assertIn(
            "grep -n -m1 -- 'name: load-super-linter-seed$'", validator
        )
        self.assertNotIn("grep -n -- '- name: dind$'", validator)
        self.assertIn(
            "did not render the dind and seed-loader init containers", validator
        )
        self.assertIn(
            "rendered the seed loader before the restartable dind init container",
            validator,
        )

    def _fake_docker(self, root: Path, image_id: str) -> Path:
        path = root / "docker"
        path.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                printf '%s\\n' "$*" >>"$DOCKER_CALLS"
                case "$1 $2" in
                  "info ") exit 0 ;;
                  "load --input") printf '%s\\n' 'Loaded image: ghcr.io/super-linter/super-linter:v8.7.0' ;;
                  "image inspect") printf '%s\\n' {image_id!r} ;;
                  "image rm") exit 0 ;;
                  *) exit 2 ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def _seed_fixture(self, root: Path, image_id: str = IMAGE_ID) -> tuple[Path, Path]:
        archive = root / "super-linter.tar.zst"
        archive.write_bytes(b"immutable archive fixture")
        checksum = subprocess.check_output(
            ["sha256sum", str(archive)], text=True
        ).split()[0]
        metadata = root / "metadata.env"
        metadata.write_text(
            "\n".join(
                (
                    f"ACTION_COMMIT={ACTION_COMMIT}",
                    f"INDEX_DIGEST={INDEX_DIGEST}",
                    f"MANIFEST_DIGEST={MANIFEST_DIGEST}",
                    f"IMAGE_ID={image_id}",
                    f"ARCHIVE_SHA256={checksum}",
                    "IMAGE_REFERENCE=ghcr.io/super-linter/super-linter:v8.7.0",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        return archive, metadata

    def test_loader_records_verified_hit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, metadata = self._seed_fixture(root)
            output = root / "result.json"
            calls = root / "calls"
            env = os.environ | {
                "PATH": f"{root}:{os.environ['PATH']}",
                "DOCKER_CALLS": str(calls),
                "SUPER_LINTER_SEED_ARCHIVE": str(archive),
                "SUPER_LINTER_SEED_METADATA": str(metadata),
                "SUPER_LINTER_SEED_RESULT": str(output),
                "SUPER_LINTER_SEED_IMAGE": "ghcr.io/f5-sales-demo/self-hosted-runner@"
                + SEED_DIGEST,
                "SUPER_LINTER_EXPECTED_ACTION_COMMIT": ACTION_COMMIT,
                "SUPER_LINTER_EXPECTED_INDEX_DIGEST": INDEX_DIGEST,
                "SUPER_LINTER_EXPECTED_MANIFEST_DIGEST": MANIFEST_DIGEST,
            }
            self._fake_docker(root, IMAGE_ID)
            result = subprocess.run(
                ["sh", str(ROOT / "scripts/load-super-linter-seed.sh")],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            evidence = json.loads(output.read_text())
            self.assertEqual("hit", evidence["result"])
            self.assertTrue(evidence["qualified"])
            self.assertEqual(INDEX_DIGEST, evidence["index_digest"])
            self.assertGreaterEqual(evidence["load_duration_seconds"], 0)

    def test_loader_removes_rejected_image_and_allows_cold_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, metadata = self._seed_fixture(root)
            output = root / "result.json"
            calls = root / "calls"
            env = os.environ | {
                "PATH": f"{root}:{os.environ['PATH']}",
                "DOCKER_CALLS": str(calls),
                "SUPER_LINTER_SEED_ARCHIVE": str(archive),
                "SUPER_LINTER_SEED_METADATA": str(metadata),
                "SUPER_LINTER_SEED_RESULT": str(output),
                "SUPER_LINTER_SEED_IMAGE": "ghcr.io/f5-sales-demo/self-hosted-runner@"
                + SEED_DIGEST,
                "SUPER_LINTER_EXPECTED_ACTION_COMMIT": ACTION_COMMIT,
                "SUPER_LINTER_EXPECTED_INDEX_DIGEST": INDEX_DIGEST,
                "SUPER_LINTER_EXPECTED_MANIFEST_DIGEST": MANIFEST_DIGEST,
            }
            self._fake_docker(root, "sha256:" + "e" * 64)
            result = subprocess.run(
                ["sh", str(ROOT / "scripts/load-super-linter-seed.sh")],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            evidence = json.loads(output.read_text())
            self.assertEqual("rejected", evidence["result"])
            self.assertFalse(evidence["qualified"])
            self.assertIn("image rm --force", calls.read_text())

    def test_qualification_contract_is_five_pair_and_fail_closed(self) -> None:
        contract = json.loads(
            (ROOT / "config/super-linter-seed-qualification.json").read_text()
        )
        self.assertEqual(5, contract["qualification"]["matched_pairs_per_state"])
        self.assertEqual(["cold", "warm"], contract["qualification"]["cache_states"])
        self.assertEqual(
            {
                "xcsh",
                "api-specs-enriched",
                "terraform-provider-xcsh",
                "multi-cloud-networking",
                "vscode-xcsh",
            },
            set(contract["repositories"]),
        )
        gates = contract["promotion_gates"]
        self.assertEqual(0.2, gates["minimum_median_pr_wall_improvement_ratio"])
        self.assertTrue(gates["p95_non_regression"])
        self.assertEqual(0.8, gates["maximum_memory_ratio"])
        self.assertEqual(0.7, gates["maximum_node_disk_ratio"])
        self.assertEqual(0.7, gates["maximum_dind_disk_ratio"])
        self.assertTrue(gates["verified_seed_hits_required"])
        self.assertTrue(gates["byte_identical_results"])


if __name__ == "__main__":
    unittest.main()
