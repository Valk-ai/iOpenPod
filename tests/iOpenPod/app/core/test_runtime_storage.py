import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from iOpenPod.app.core.runtime import configure_runtime_storage
from iOpenPod.app.core.settings.paths import default_cache_path


def test_default_cache_path() -> None:
    path = default_cache_path()
    assert isinstance(path, Path)
    assert path.is_absolute()


def test_configure_runtime_storage(tmp_path: Path) -> None:
    fake_cache = tmp_path / "cache"
    fake_cache.mkdir(parents=True)
    staging = fake_cache / "staging"
    staging.mkdir()

    # Create stale directory and file to verify cleanup
    stale_dir = staging / "iopenpod-media-stale"
    stale_dir.mkdir()
    (stale_dir / "junk.m4a").write_bytes(b"junk")
    stale_file = staging / "iopenpod-content-stale.bin"
    stale_file.write_bytes(b"junk")

    with patch(
        "iOpenPod.app.core.settings.paths.default_cache_path",
        return_value=fake_cache,
    ):
        result_dir = configure_runtime_storage()

    assert result_dir == staging
    assert os.environ["TMPDIR"] == str(staging)
    assert tempfile.tempdir == str(staging)
    assert not stale_dir.exists()
    assert not stale_file.exists()
