"""Release ordering must not move latest back to an older version."""

import pytest

from scripts.image_tags import image_metadata


def test_newest_release_uses_numeric_order_and_lowercase_repository():
    result = image_metadata(
        "AhmedForge/DistribuQueue", "v1.10.0", "a" * 40, ["v1.9.9", "v1.10.0"]
    )
    assert result == {
        "prefix": "ghcr.io/ahmedforge/distribuqueue",
        "version": "v1.10.0",
        "sha": "sha-aaaaaaa",
        "latest": "true",
    }


def test_older_release_does_not_replace_latest():
    assert (
        image_metadata("owner/repo", "v1.2.0", "a" * 40, ["v1.2.0", "v2.0.0"])["latest"]
        == "false"
    )


def test_prerelease_tags_do_not_advance_latest():
    assert (
        image_metadata("owner/repo", "v1.2.0", "a" * 40, ["v1.2.0", "v2.0.0-rc.1"])[
            "latest"
        ]
        == "true"
    )


@pytest.mark.parametrize("version", ["v1.2.3-rc.1", "v01.2.3", "v1.2.3/extra"])
def test_nonstable_or_malformed_version_is_rejected(version):
    with pytest.raises(ValueError, match="stable"):
        image_metadata("owner/repo", version, "a" * 40, [])
