"""Unit tests for Jellyfin library adapter and merge policies."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from PIL import Image

if TYPE_CHECKING:
    from pathlib import Path

from iOpenPod.app.host_media_library import (
    HostMediaCacheStats,
    HostMediaFileKind,
    HostMediaLibrary,
    HostMediaSource,
)
from iOpenPod.app.jellyfin.library import (
    download_jellyfin_artwork,
    download_jellyfin_track,
    merge_with_jellyfin,
    stable_jellyfin_id,
    translate_jellyfin_item,
)
from iOpenPod.app.jellyfin.models import (
    JellyfinItem,
    OverlapPolicy,
)
from iPodDB.library import (
    LibrarySnapshot,
    MediaKind,
    MediaType,
    Track,
    TrackMetadata,
)
from storage import HostPath


def test_stable_jellyfin_id() -> None:
    id1 = stable_jellyfin_id("track-abc")
    id2 = stable_jellyfin_id("track-abc")
    id3 = stable_jellyfin_id("track-xyz")

    assert id1 == id2
    assert id1 != id3
    assert id1 >= 1_000_000_000


def test_translate_jellyfin_item(tmp_path: Path) -> None:
    item = JellyfinItem(
        item_id="track-42",
        name="Hotel California",
        album="Hotel California",
        artist="Eagles",
        album_artist="Eagles",
        duration_ms=390000,
        track_number=1,
        disc_number=1,
        year=1976,
        genres=("Classic Rock",),
        media_type="Audio",
        container="flac",
        size_bytes=42000000,
        bitrate_bps=860000,
        image_tag="primary-tag",
    )

    media_item = translate_jellyfin_item(item, tmp_path)
    assert media_item.track.title == "Hotel California"
    assert media_item.track.artist == "Eagles"
    assert media_item.track.length_ms == 390000
    assert media_item.track.track_number == 1
    assert media_item.track.media_types == (MediaType.AUDIO,)
    assert media_item.track.media_kind is MediaKind.MUSIC
    assert media_item.track.artwork_id > 0
    assert media_item.source.kind is HostMediaFileKind.AUDIO
    assert media_item.source.size_bytes == 42000000
    assert media_item.cache_path == tmp_path / "track-42.flac"


def test_translate_jellyfin_item_uses_existing_cache_file_stat(tmp_path: Path) -> None:
    cache_file = tmp_path / "track-cached.flac"
    cache_file.write_bytes(b"FLACDATA12345")
    stat = cache_file.stat()

    item = JellyfinItem(
        item_id="track-cached",
        name="Cached Song",
        album="Album",
        artist="Artist",
        container="flac",
        size_bytes=999999,
    )

    media_item = translate_jellyfin_item(item, tmp_path)
    assert media_item.source.size_bytes == stat.st_size
    assert media_item.source.modified_ns == stat.st_mtime_ns


def _make_dummy_local_library(tmp_path: Path) -> HostMediaLibrary:
    track1 = Track(
        track_id=1,
        title="Bohemian Rhapsody",
        artist="Queen",
        album="A Night at the Opera",
        album_artist="Queen",
        track_number=11,
        length_ms=354000,
        metadata=TrackMetadata(location=str(tmp_path / "bohemian.mp3")),
    )
    source1 = HostMediaSource(
        path=HostPath(tmp_path / "bohemian.mp3"),
        kind=HostMediaFileKind.AUDIO,
        size_bytes=10000000,
        modified_ns=1000,
    )
    snapshot = LibrarySnapshot(tracks=(track1,))
    return HostMediaLibrary(
        snapshot=snapshot,
        sources=(source1,),
        issues=(),
        cache=HostMediaCacheStats(),
    )


def test_merge_with_jellyfin_prefer_local(tmp_path: Path) -> None:
    local_lib = _make_dummy_local_library(tmp_path)

    # Remote item 1: Exact match with local Bohemian Rhapsody
    remote1 = translate_jellyfin_item(
        JellyfinItem(
            item_id="jelly-1",
            name="Bohemian Rhapsody",
            artist="Queen",
            album="A Night at the Opera",
            track_number=11,
            media_type="Audio",
        ),
        tmp_path,
    )

    # Remote item 2: Unique track
    remote2 = translate_jellyfin_item(
        JellyfinItem(
            item_id="jelly-2",
            name="Killer Queen",
            artist="Queen",
            album="Sheer Heart Attack",
            track_number=2,
            media_type="Audio",
        ),
        tmp_path,
    )

    merged = merge_with_jellyfin(
        local_lib,
        (remote1, remote2),
        policy=OverlapPolicy.PREFER_LOCAL,
    )

    # Should have 2 tracks total: local Bohemian Rhapsody + remote Killer Queen
    assert len(merged.snapshot.tracks) == 2
    titles = [t.title for t in merged.snapshot.tracks]
    assert "Bohemian Rhapsody" in titles
    assert "Killer Queen" in titles

    # Bohemian Rhapsody should retain local track ID and location
    boh = next(t for t in merged.snapshot.tracks if t.title == "Bohemian Rhapsody")
    assert boh.track_id == 1
    assert "bohemian.mp3" in boh.metadata.location


def test_merge_with_jellyfin_prefer_jellyfin(tmp_path: Path) -> None:
    local_lib = _make_dummy_local_library(tmp_path)

    remote1 = translate_jellyfin_item(
        JellyfinItem(
            item_id="jelly-1",
            name="Bohemian Rhapsody",
            artist="Queen",
            album="A Night at the Opera",
            track_number=11,
            media_type="Audio",
        ),
        tmp_path,
    )

    merged = merge_with_jellyfin(
        local_lib,
        (remote1,),
        policy=OverlapPolicy.PREFER_JELLYFIN,
    )

    assert len(merged.snapshot.tracks) == 1
    boh = merged.snapshot.tracks[0]
    # Replaced by remote track ID
    assert boh.track_id == remote1.track.track_id


def test_merge_with_jellyfin_prompt(tmp_path: Path) -> None:
    local_lib = _make_dummy_local_library(tmp_path)

    remote1 = translate_jellyfin_item(
        JellyfinItem(
            item_id="jelly-1",
            name="Bohemian Rhapsody",
            artist="Queen",
            album="A Night at the Opera",
            track_number=11,
            media_type="Audio",
        ),
        tmp_path,
    )

    merged = merge_with_jellyfin(
        local_lib,
        (remote1,),
        policy=OverlapPolicy.PROMPT,
    )

    # Both copies are retained as separate items so duplicate dialog can resolve
    assert len(merged.snapshot.tracks) == 2


def test_download_jellyfin_track(tmp_path: Path) -> None:
    media_item = translate_jellyfin_item(
        JellyfinItem(
            item_id="track-down-1",
            name="Song",
            media_type="Audio",
            container="mp3",
        ),
        tmp_path,
    )

    fake_client = MagicMock()
    fake_client.get_download_url.return_value = (
        "http://jelly.lan/Items/track-down-1/Download"
    )
    fake_client._headers.return_value = {}

    fake_resp = MagicMock()
    fake_resp.read.side_effect = [b"MOCK AUDIO DATA CONTENT", b""]
    fake_resp.headers = {"Content-Length": "24"}

    with patch("iOpenPod.app.jellyfin.library.urlopen", return_value=fake_resp):
        out_path = download_jellyfin_track(fake_client, media_item)

    assert out_path.exists()
    assert out_path.read_bytes() == b"MOCK AUDIO DATA CONTENT"


def _make_dummy_jpeg() -> bytes:
    img = Image.new("RGB", (10, 10), color="blue")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_download_jellyfin_artwork_success(tmp_path: Path) -> None:
    fake_client = MagicMock()
    fake_client.get_image_url.return_value = (
        "http://jelly.lan/Items/item-1/Images/Primary"
    )
    fake_client._headers.return_value = {"Accept": "application/json"}

    jpeg_bytes = _make_dummy_jpeg()
    fake_resp = MagicMock()
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.read.return_value = jpeg_bytes

    with patch("iOpenPod.app.jellyfin.library.urlopen", return_value=fake_resp):
        out_path = download_jellyfin_artwork(fake_client, "item-1", tmp_path)

    assert out_path is not None
    assert out_path.is_file()
    assert out_path == tmp_path / "item-1.jpg"
    assert out_path.read_bytes() == jpeg_bytes


def test_download_jellyfin_artwork_cached(tmp_path: Path) -> None:
    cached_file = tmp_path / "cached-item.jpg"
    cached_file.write_bytes(b"EXISTING_ART")

    fake_client = MagicMock()

    with patch("iOpenPod.app.jellyfin.library.urlopen") as mock_url:
        out_path = download_jellyfin_artwork(fake_client, "cached-item", tmp_path)

    mock_url.assert_not_called()
    assert out_path == cached_file


def test_download_jellyfin_artwork_failure(tmp_path: Path) -> None:
    fake_client = MagicMock()
    fake_client.get_image_url.return_value = (
        "http://jelly.lan/Items/item-missing/Images/Primary"
    )
    fake_client._headers.return_value = {}

    with patch(
        "iOpenPod.app.jellyfin.library.urlopen", side_effect=Exception("404 Not Found")
    ):
        out_path = download_jellyfin_artwork(fake_client, "item-missing", tmp_path)

    assert out_path is None
    assert not (tmp_path / "item-missing.jpg").exists()


def test_download_jellyfin_artwork_corrupt(tmp_path: Path) -> None:
    fake_client = MagicMock()
    fake_client.get_image_url.return_value = (
        "http://jelly.lan/Items/item-corrupt/Images/Primary"
    )
    fake_client._headers.return_value = {}

    fake_resp = MagicMock()
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.read.return_value = b"NOT AN IMAGE"

    with patch("iOpenPod.app.jellyfin.library.urlopen", return_value=fake_resp):
        out_path = download_jellyfin_artwork(fake_client, "item-corrupt", tmp_path)

    assert out_path is None
    assert not (tmp_path / "item-corrupt.jpg").exists()
