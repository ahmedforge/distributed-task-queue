"""Version-tag validation and the newest-stable-release policy for GHCR."""

import os
import re
import subprocess


def image_metadata(repository, version, sha, existing_tags):
    pattern = r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    match = re.fullmatch(pattern, version)
    if not match:
        raise ValueError("Publish only stable vMAJOR.MINOR.PATCH tags")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a full Git commit SHA")
    versions = [
        tuple(map(int, m.groups()))
        for tag in existing_tags
        if (m := re.fullmatch(pattern, tag))
    ]
    current = tuple(map(int, match.groups()))
    return {
        "prefix": "ghcr.io/" + repository.lower(),
        "version": version,
        "sha": "sha-" + sha[:7],
        "latest": str(current >= max(versions, default=current)).lower(),
    }


def main():
    tags = subprocess.check_output(["git", "tag", "--list"], text=True).splitlines()
    result = image_metadata(
        os.environ["GITHUB_REPOSITORY"],
        os.environ["GITHUB_REF_NAME"],
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        tags,
    )
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for key, value in result.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
