#!/usr/bin/env python3
"""Verify the SPDX payload selected from an immutable image's Linux/amd64 SBOM."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlparse


class SbomValidationError(ValueError):
    """A fixed structural diagnostic that contains no input payload."""


def validate(payload: object) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("SPDX"), dict):
        raise SbomValidationError("missing SPDX document")
    document = payload["SPDX"]
    if document.get("spdxVersion") not in ("SPDX-2.2", "SPDX-2.3"):
        raise SbomValidationError("unsupported SPDX version")
    if document.get("SPDXID") != "SPDXRef-DOCUMENT":
        raise SbomValidationError("invalid SPDX document identity")
    if document.get("dataLicense") != "CC0-1.0":
        raise SbomValidationError("invalid SPDX data license")
    if not isinstance(document.get("name"), str) or not document["name"].strip():
        raise SbomValidationError("missing SPDX document name")
    namespace = document.get("documentNamespace")
    if not isinstance(namespace, str) or not urlparse(namespace).scheme:
        raise SbomValidationError("invalid SPDX document namespace")
    creation = document.get("creationInfo")
    if not isinstance(creation, dict):
        raise SbomValidationError("missing SPDX creation information")
    if not isinstance(creation.get("created"), str) or not creation["created"]:
        raise SbomValidationError("missing SPDX creation time")
    creators = creation.get("creators")
    if not isinstance(creators, list) or not creators or not all(
        isinstance(creator, str) and creator.strip() for creator in creators
    ):
        raise SbomValidationError("missing SPDX creator")
    packages = document.get("packages")
    if not isinstance(packages, list) or not packages:
        raise SbomValidationError("empty SPDX package inventory")
    identities: set[str] = set()
    for package in packages:
        if not isinstance(package, dict):
            raise SbomValidationError("invalid SPDX package")
        name = package.get("name")
        identity = package.get("SPDXID")
        if not isinstance(name, str) or not name.strip():
            raise SbomValidationError("missing SPDX package name")
        if not isinstance(identity, str) or not re.fullmatch(r"SPDXRef-[A-Za-z0-9.-]+", identity):
            raise SbomValidationError("invalid SPDX package identity")
        if identity in identities:
            raise SbomValidationError("duplicate SPDX package identity")
        identities.add(identity)
    return len(packages)


def load_archive(path: Path) -> tuple[object, dict[str, object]]:
    """Read attributed attestations without extracting an OCI archive."""
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise SbomValidationError("ambiguous OCI archive members")
        lookup = {member.name: member for member in members}

        def read_json(name: str, descriptor: dict[str, object] | None = None) -> tuple[dict[str, object], bytes]:
            member = lookup.get(name)
            if member is None or not member.isfile():
                raise SbomValidationError("missing OCI metadata blob")
            stream = archive.extractfile(member)
            if stream is None:
                raise SbomValidationError("unreadable OCI metadata blob")
            with stream:
                data = stream.read()
            if descriptor is not None:
                digest = descriptor.get("digest")
                if descriptor.get("size") != len(data) or digest != "sha256:" + hashlib.sha256(data).hexdigest():
                    raise SbomValidationError("OCI metadata digest or size mismatch")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise SbomValidationError("invalid OCI metadata object")
            return value, data

        def read_blob(descriptor: dict[str, object]) -> tuple[dict[str, object], bytes]:
            digest = descriptor.get("digest")
            if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise SbomValidationError("invalid OCI descriptor digest")
            return read_json("blobs/sha256/" + digest.removeprefix("sha256:"), descriptor)

        images: set[str] = set()
        attestations: list[tuple[dict[str, object], dict[str, object]]] = []

        def walk(index: dict[str, object]) -> None:
            manifests = index.get("manifests")
            if not isinstance(manifests, list):
                raise SbomValidationError("invalid OCI image index")
            for descriptor in manifests:
                if not isinstance(descriptor, dict):
                    raise SbomValidationError("invalid OCI image descriptor")
                manifest, _ = read_blob(descriptor)
                if "manifests" in manifest:
                    walk(manifest)
                    continue
                annotations = descriptor.get("annotations", {})
                if not isinstance(annotations, dict):
                    raise SbomValidationError("invalid OCI descriptor annotations")
                if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
                    attestations.append((descriptor, manifest))
                elif descriptor.get("platform") == {"os": "linux", "architecture": "amd64"}:
                    images.add(str(descriptor["digest"]))

        index, _ = read_json("index.json")
        walk(index)
        if len(images) != 1:
            raise SbomValidationError("missing or ambiguous Linux/amd64 image")
        image = next(iter(images))
        documents: list[tuple[object, bytes]] = []
        for descriptor, manifest in attestations:
            annotations = descriptor["annotations"]
            if annotations.get("vnd.docker.reference.digest") != image:
                raise SbomValidationError("foreign OCI attestation attribution")
            layers = manifest.get("layers")
            if not isinstance(layers, list):
                raise SbomValidationError("invalid OCI attestation layers")
            for layer in layers:
                if not isinstance(layer, dict):
                    raise SbomValidationError("invalid OCI attestation layer")
                if layer.get("mediaType") != "application/vnd.in-toto+json":
                    continue
                statement, raw = read_blob(layer)
                if statement.get("predicateType") != "https://spdx.dev/Document":
                    continue
                subjects = statement.get("subject")
                if not isinstance(subjects, list) or not subjects or not all(
                    isinstance(subject, dict)
                    and isinstance(subject.get("digest"), dict)
                    and subject["digest"].get("sha256") == image.removeprefix("sha256:")
                    for subject in subjects
                ):
                    raise SbomValidationError("foreign SPDX statement subject")
                documents.append((statement.get("predicate"), raw))
        if len(documents) != 1:
            raise SbomValidationError("missing or ambiguous SPDX attestation")
        document, raw = documents[0]
        return {"SPDX": document}, {
            "sourceManifest": image,
            "sbomBytes": len(raw),
            "sbomSha256": hashlib.sha256(raw).hexdigest(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oci-archive", type=Path)
    args = parser.parse_args()
    try:
        if args.oci_archive is None:
            payload, proof = json.load(sys.stdin), {}
        else:
            payload, proof = load_archive(args.oci_archive)
        count = validate(payload)
    except SbomValidationError as error:
        print(f"registry SPDX SBOM validation failed: {error}", file=sys.stderr)
        return 1
    except (ValueError, TypeError, OSError, KeyError, tarfile.TarError):
        print("registry SPDX SBOM is missing, malformed, or incomplete", file=sys.stderr)
        return 1
    details = " ".join(f"{name}={value}" for name, value in proof.items())
    print(f"verified SPDX SBOM packages={count}" + (" " + details if details else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
