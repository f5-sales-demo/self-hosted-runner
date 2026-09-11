#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ImageContractTests(unittest.TestCase):
    def test_catalog_integrity_fields_are_immutable(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["schema_version"], 1)
        self.assertRegex(catalog["upstream_runner_images"]["revision"], r"^[0-9a-f]{40}$")
        self.assertGreaterEqual(len(catalog["setup_actions"]), 5)
        for tool in catalog["tools"]:
            self.assertTrue(tool["profiles"])
            self.assertTrue(tool["command"])
            if "sha256" in tool:
                self.assertRegex(tool["sha256"], r"^[0-9a-f]{64}$")

    def test_standard_target_has_no_docker_client(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        standard = dockerfile.split("FROM runner-base AS standard", 1)[1].split("FROM runner-base AS container-build", 1)[0]
        container_build = dockerfile.split("FROM runner-base AS container-build", 1)[1]
        self.assertNotIn("docker", standard.lower())
        self.assertIn("docker-buildx", container_build)
        self.assertIn("docker-compose", container_build)
        self.assertIn("USER runner", standard)
        self.assertIn("RUNNER_MANUALLY_TRAP_SIG=1", dockerfile)
        self.assertIn("ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT=1", dockerfile)
        self.assertIn("scripts/runner-profile.py /usr/local/bin/runner-profile", dockerfile)
        self.assertIn("scripts/docker-action-profile.py /usr/local/bin/docker-action-profile", dockerfile)
        self.assertIn("schemas/workload-profile.schema.json /usr/local/share/runner-profile/workload-profile.schema.json", dockerfile)
        self.assertIn("schemas/docker-action-profile.schema.json /usr/local/share/runner-profile/docker-action-profile.schema.json", dockerfile)
        self.assertIn("/home/runner /opt/actions-runner /runner-runtime", dockerfile)
        self.assertIn("tar --extract --gzip --file /tmp/actions-runner.tar.gz --directory /opt/actions-runner", dockerfile)
        self.assertIn("cp --archive --link /opt/actions-runner/. /home/runner/", dockerfile)
        self.assertIn("chown -R runner:runner /home/runner /opt/actions-runner", dockerfile)
        self.assertIn("find /home/runner -mindepth 1 -maxdepth 1", (ROOT / "scripts/runner-entrypoint.sh").read_text(encoding="utf-8"))
        verifier = (ROOT / "scripts/verify-tools.py").read_text(encoding="utf-8")
        self.assertIn('(Path("/home/runner"), Path("/opt/actions-runner"))', verifier)
        self.assertIn('Path("/opt/actions-runner").is_symlink()', verifier)
        self.assertNotIn("--output /tmp/gcloud.tar.gz /tmp/", dockerfile)
        self.assertIn("--output /tmp/uv.tar.gz", dockerfile)

    def test_only_github_hosted_workflows_build_or_publish(self) -> None:
        verify = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
        publish = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-24.04", verify)
        self.assertIn("runs-on: ubuntu-24.04", publish)
        self.assertIn("--no-cache", verify)
        self.assertIn("--no-cache", publish)
        self.assertIn("--provenance=mode=max", publish)
        self.assertNotIn("--sbom=true", publish)
        self.assertIn("docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e", publish)
        self.assertIn("driver: docker-container", publish)
        self.assertIn("actions/attest-build-provenance@", publish)
        self.assertIn("docker buildx imagetools inspect --format", publish)
        self.assertNotIn("containerimage.digest", publish)
        self.assertNotIn("actions/attest-sbom@", publish)
        self.assertNotIn("SPDX SBOM", (ROOT / "scripts/verify-promotion.sh").read_text(encoding="utf-8"))
        self.assertNotRegex(verify, r"runs-on:\s*\[?self-hosted")
        self.assertNotRegex(publish, r"runs-on:\s*\[?self-hosted")
        self.assertEqual(2, verify.count("--network none --user 1001:1001 --entrypoint verify-runner-tools"))

    def test_no_bun_qualification_image_or_manual_publisher_remains(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        publish = (ROOT / ".github/workflows/publish.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("compute-bun", dockerfile)
        self.assertNotIn("BUN_CANDIDATE", dockerfile)
        self.assertNotIn("compute-bun", publish)
        self.assertFalse((ROOT / "scripts/configure-bun-candidate.py").exists())

    def test_snapshot_packages_are_verified_and_fetched_concurrently(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("apt-get --print-uris --yes --no-install-recommends install $packages", dockerfile)
        self.assertIn("/usr/lib/apt/apt-helper", dockerfile)
        self.assertIn("download-file", dockerfile)
        self.assertIn("xargs -r -n3 -P16", dockerfile)
        self.assertIn('"$0" "/var/cache/apt/archives/partial/$1" "$2"', dockerfile)
        self.assertIn("apt-get --no-download --yes --no-install-recommends install $packages", dockerfile)
        self.assertIn("/var/cache/apt/archives/*.deb /var/cache/apt/archives/partial/*", dockerfile)
        self.assertIn("for attempt in 1 2 3 4 5 6 7 8", dockerfile)

    def test_pr_image_validation_is_cancellable_without_affecting_publication(self) -> None:
        verify = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
        publish = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("self-hosted-runner-pr-image-validation", verify)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", verify)
        self.assertIn("publish-immutable-self-hosted-runner", publish)
        self.assertIn("cancel-in-progress: false", publish)

    def test_ruff_action_is_preloaded_in_the_immutable_tool_cache(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        ruff_action = catalog["setup_actions"]["astral-sh/ruff-action"]
        self.assertEqual(["0.16.0"], ruff_action["versions"])
        self.assertEqual(
            "/opt/hostedtoolcache/ruff/0.16.0/x86_64.complete",
            ruff_action["cache_paths"]["0.16.0"],
        )
        ruff = next(tool for tool in catalog["tools"] if tool["name"] == "ruff")
        self.assertEqual("0.16.0", ruff["version"])
        self.assertRegex(ruff["sha256"], r"^[0-9a-f]{64}$")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG RUFF_VERSION=0.16.0", dockerfile)
        self.assertIn('"$AGENT_TOOLSDIRECTORY/ruff/${RUFF_VERSION}/x86_64.complete"', dockerfile)

    def test_setup_uv_is_preloaded_in_the_immutable_tool_cache(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        setup_uv = catalog["setup_actions"]["astral-sh/setup-uv"]
        self.assertEqual(["0.8.24"], setup_uv["versions"])
        self.assertEqual(
            "/opt/hostedtoolcache/uv/0.8.24/x86_64.complete",
            setup_uv["cache_paths"]["0.8.24"],
        )
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('"$AGENT_TOOLSDIRECTORY/uv/${UV_VERSION}/x86_64.complete"', dockerfile)
        self.assertIn('"$AGENT_TOOLSDIRECTORY/uv/${UV_VERSION}/x86_64/uv"', dockerfile)

    def test_setup_go_uses_the_action_compatible_lowercase_cache_path(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        setup_go = catalog["setup_actions"]["actions/setup-go"]
        self.assertEqual(
            "/opt/hostedtoolcache/go/1.25.12/x64.complete",
            setup_go["cache_path"],
        )
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('"$AGENT_TOOLSDIRECTORY/go/1.25.12/x64.complete"', dockerfile)
        self.assertNotIn('"$AGENT_TOOLSDIRECTORY/Go/1.25.12/x64.complete"', dockerfile)

    def test_setup_python_cache_has_python_and_pip_entrypoints(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts/verify-tools.py").read_text(encoding="utf-8")
        self.assertIn("ln -s python3 /opt/python-${PYTHON311_VERSION}/bin/python", dockerfile)
        self.assertIn("ln -s pip3 /opt/python-${PYTHON311_VERSION}/bin/pip", dockerfile)
        self.assertIn("ln -s python3 /opt/python-${PYTHON313_VERSION}/bin/python", dockerfile)
        self.assertIn("ln -s pip3 /opt/python-${PYTHON313_VERSION}/bin/pip", dockerfile)
        self.assertIn("cache PATH did not resolve its python and pip entrypoints", verifier)

    def test_pnpm_spectral_and_actionlint_are_immutable_image_tools(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        tools = {tool["name"]: tool for tool in catalog["tools"]}
        self.assertEqual("11.3.0", tools["pnpm"]["version"])
        self.assertEqual("6.16.3", tools["spectral"]["version"])
        self.assertEqual("1.7.12", tools["actionlint"]["version"])
        self.assertEqual(
            "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
            tools["actionlint"]["sha256"],
        )
        self.assertEqual(["standard", "container-build"], tools["actionlint"]["profiles"])
        self.assertEqual("actionlint -version", tools["actionlint"]["command"])
        self.assertEqual(["11.3.0"], catalog["setup_actions"]["pnpm/action-setup"]["versions"])
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG PNPM_VERSION=11.3.0", dockerfile)
        self.assertIn("ARG ACTIONLINT_VERSION=1.7.12", dockerfile)
        self.assertIn("ARG ACTIONLINT_SHA256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8", dockerfile)
        self.assertIn("actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz", dockerfile)
        self.assertIn("install -m 0555 /tmp/actionlint /usr/local/bin/actionlint", dockerfile)
        self.assertIn("exec node /opt/pnpm/package/bin/pnpm.cjs", dockerfile)
        self.assertIn("npm ci --omit=dev --ignore-scripts --no-audit --no-fund", dockerfile)

    def test_xcsh_linux_test_dependencies_are_immutable_image_tools(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts/verify-tools.py").read_text(encoding="utf-8")
        for package in (
            "clang", "libcairo2-dev", "libpango1.0-dev", "libjpeg-dev", "libgif-dev",
            "librsvg2-dev", "fd-find", "ripgrep", "imagemagick", "rustup",
            "gcc-aarch64-linux-gnu", "libc6-dev-arm64-cross", "xvfb", "xauth",
        ):
            self.assertIn(package, dockerfile)
        self.assertIn("aarch64-linux-gnu-gcc -x c - -o /tmp/runner-arm64-libc-smoke", verifier)
        self.assertIn("runner-{}-smoke", verifier)
        self.assertIn("ln -s /usr/bin/fdfind /usr/local/bin/fd", dockerfile)
        self.assertIn("ln -s /usr/bin/convert /usr/local/bin/magick", dockerfile)
        self.assertIn("USER runner", dockerfile)
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        rustup = next(tool for tool in catalog["tools"] if tool["name"] == "rustup")
        self.assertEqual("rustup --version", rustup["command"])
        clang = next(tool for tool in catalog["tools"] if tool["name"] == "clang")
        clangxx = next(tool for tool in catalog["tools"] if tool["name"] == "clang++")
        self.assertEqual("18.1.3", clang["version"])
        self.assertEqual("clang --version", clang["command"])
        self.assertEqual("clang++ --version", clangxx["command"])
        xvfb = next(tool for tool in catalog["tools"] if tool["name"] == "xvfb-run")
        self.assertEqual("xvfb-run --help", xvfb["command"])
        self.assertEqual(["standard", "container-build"], xvfb["profiles"])

    def test_zig_is_a_pinned_immutable_image_tool(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        zig = next(tool for tool in catalog["tools"] if tool["name"] == "zig")
        self.assertEqual("0.16.0", zig["version"])
        self.assertEqual(
            "70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00",
            zig["sha256"],
        )
        self.assertEqual(["standard", "container-build"], zig["profiles"])
        self.assertEqual("zig version", zig["command"])
        self.assertEqual("0.16.0", zig["expected"])

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG ZIG_VERSION=0.16.0", dockerfile)
        self.assertIn(
            "ARG ZIG_SHA256=70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00",
            dockerfile,
        )
        self.assertIn(
            'https://ziglang.org/download/${ZIG_VERSION}/zig-x86_64-linux-${ZIG_VERSION}.tar.xz',
            dockerfile,
        )
        self.assertIn('echo "${ZIG_SHA256}  /tmp/zig.tar.xz" | sha256sum --check --strict', dockerfile)
        self.assertIn("ln -s /opt/zig/zig /usr/local/bin/zig", dockerfile)

    def test_xcsh_performance_toolchain_is_baked_and_pinned(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        tools = {tool["name"]: tool for tool in catalog["tools"]}
        expected_versions = {
            "bun": "1.4.2",
            "zig": "0.16.0",
            "rust-toolchain-manifest": "nightly-2026-09-03",
            "rustc": "1.100.0-nightly (2e2b193f8 2026-09-02)",
            "cargo": "0.101.0-nightly (b2e9d5f9d 2026-09-02)",
            "rustfmt": "nightly-2026-09-03",
            "clippy": "nightly-2026-09-03",
            "rust-analyzer": "nightly-2026-09-03",
            "rust-std-linux-x64": "nightly-2026-09-03",
            "rust-std-windows-x64": "nightly-2026-09-03",
            "rust-std-linux-arm64": "nightly-2026-09-03",
            "cargo-nextest": "0.9.143",
            "llvm-nm": "18.1.3",
        }
        for name, version in expected_versions.items():
            self.assertEqual(version, tools[name]["version"])
        self.assertEqual("cargo 1.100.0-nightly", tools["cargo"]["expected"])
        self.assertEqual("fdfind ", tools["fd"]["expected"])
        for name in expected_versions.keys() - {"llvm-nm"}:
            self.assertRegex(tools[name]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(["1.4.2"], catalog["setup_actions"]["oven-sh/setup-bun"]["versions"])

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG BUN_VERSION=1.4.2", dockerfile)
        self.assertIn("ARG RUST_TOOLCHAIN=nightly-2026-09-03", dockerfile)
        self.assertIn("ARG CARGO_NEXTEST_VERSION=0.9.143", dockerfile)
        self.assertIn("--component rustfmt --component clippy --component rust-analyzer", dockerfile)
        for target in ("x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc", "aarch64-unknown-linux-gnu"):
            self.assertIn(f"--target {target}", dockerfile)
        self.assertIn("packages='ant bash build-essential clang", dockerfile)
        self.assertIn(" locales llvm make ", dockerfile)
        self.assertNotIn("cargo install cargo-nextest", dockerfile)

        verifier = (ROOT / "scripts/verify-tools.py").read_text(encoding="utf-8")
        self.assertIn("os.geteuid() != 1001", verifier)
        self.assertIn('Path("/opt/cargo/registry")', verifier)

if __name__ == "__main__":
    unittest.main()
