"""Adapter to translate Jellyfin server items into iOpenPod Library models."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

from iOpenPod.app.host_media_library import (
    HostMediaFileKind,
    HostMediaLibrary,
    HostMediaSource,
)
from iOpenPod.app.jellyfin.models import (
    JellyfinConfig,
    JellyfinItem,
    OverlapPolicy,
)
from iOpenPod.app.models.artwork import ArtworkImage, ArtworkRequest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from iOpenPod.app.jellyfin.client import JellyfinClient
from iPodDB.library import (
    LibrarySnapshot,
    MediaType,
    Track,
    TrackMetadata,
)
from storage import HostPath

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "iopenpod" / "jellyfin"


def is_jellyfin_source(location: str | Path | object | None) -> bool:
    """Return True if the given media location or path is in the Jellyfin cache."""
    if not location:
        return False
    if hasattr(location, "path"):
        location = location.path
    try:
        loc_str = os.fspath(location)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    cache_str = os.fspath(DEFAULT_CACHE_DIR)
    return loc_str.startswith(cache_str) or ".cache/iopenpod/jellyfin" in loc_str


def stable_jellyfin_id(item_id: str) -> int:
    """Compute a deterministic 31-bit positive integer ID for a Jellyfin item GUID."""
    digest = hashlib.sha256(item_id.encode("utf-8")).digest()
    # 31-bit unsigned integer in high range to avoid colliding with local tracks
    num = int.from_bytes(digest[:4], byteorder="big") & 0x7FFFFFFF
    return 1_000_000_000 + (num % 1_000_000_000)


@dataclass(frozen=True, slots=True)
class JellyfinMediaItem:
    """One Jellyfin media item mapped to iOpenPod Track and HostMediaSource."""

    item: JellyfinItem
    track: Track
    source: HostMediaSource
    cache_path: Path


def translate_jellyfin_item(
    item: JellyfinItem,
    cache_dir: Path,
) -> JellyfinMediaItem:
    """Map a raw JellyfinItem into an iOpenPod Track and HostMediaSource."""
    track_id = stable_jellyfin_id(item.item_id)
    extension = item.container.lstrip(".").lower() if item.container else "mp3"
    cache_file = cache_dir / f"{item.item_id}.{extension}"

    media_types = (MediaType.AUDIO,) if item.is_audio else (MediaType.VIDEO,)

    art_guid = item.album_id or item.item_id
    has_image = bool(item.image_tag or item.album_primary_image_tag)
    artwork_id = stable_jellyfin_id(art_guid) if has_image else 0

    track = Track(
        track_id=track_id,
        title=item.name or "Unknown Title",
        artist=item.artist or "Unknown Artist",
        album_artist=item.album_artist or item.artist or "Unknown Artist",
        album=item.album or "Unknown Album",
        genre=item.genres[0] if item.genres else "",
        year=item.year,
        track_number=item.track_number,
        length_ms=item.duration_ms,
        bitrate_kbps=item.bitrate_bps // 1000 if item.bitrate_bps else 0,
        media_types=media_types,
        artwork_id=artwork_id,
        metadata=TrackMetadata(
            disc_number=item.disc_number,
            location=os.fspath(cache_file),
        ),
    )

    kind = HostMediaFileKind.AUDIO if item.is_audio else HostMediaFileKind.VIDEO
    if cache_file.is_file():
        stat = cache_file.stat()
        size_bytes = stat.st_size
        modified_ns = stat.st_mtime_ns
    else:
        size_bytes = item.size_bytes or 1
        modified_ns = 0

    source = HostMediaSource(
        path=HostPath(cache_file),
        kind=kind,
        size_bytes=size_bytes,
        modified_ns=modified_ns,
    )

    return JellyfinMediaItem(
        item=item,
        track=track,
        source=source,
        cache_path=cache_file,
    )


def fetch_jellyfin_library(
    client: JellyfinClient,
    config: JellyfinConfig,
    *,
    sync_videos: bool = False,
    cache_dir: Path | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[JellyfinMediaItem, ...]:
    """Retrieve media items from Jellyfin and map them to JellyfinMediaItem records."""
    if not config.is_authenticated:
        return ()

    target_cache_dir = cache_dir or DEFAULT_CACHE_DIR
    target_cache_dir.mkdir(parents=True, exist_ok=True)

    items: list[JellyfinItem] = []
    if checkpoint:
        checkpoint()

    if config.sync_music:
        items.extend(client.get_music_tracks(config.user_id))

    if checkpoint:
        checkpoint()

    if config.sync_videos and sync_videos:
        items.extend(client.get_video_items(config.user_id))

    results: list[JellyfinMediaItem] = []
    for raw in items:
        if checkpoint:
            checkpoint()
        results.append(translate_jellyfin_item(raw, target_cache_dir))

    return tuple(results)


def _track_signature(track: Track) -> tuple[str, str, str, int]:
    """Normalize track metadata into a comparison key for overlap detection."""
    return (
        track.artist.casefold().strip(),
        track.album.casefold().strip(),
        track.title.casefold().strip(),
        track.track_number,
    )


def merge_with_jellyfin(
    local_library: HostMediaLibrary,
    remote_items: tuple[JellyfinMediaItem, ...],
    policy: OverlapPolicy = OverlapPolicy.PREFER_LOCAL,
) -> HostMediaLibrary:
    """Merge remote Jellyfin media items into an existing HostMediaLibrary according to policy."""
    if not remote_items:
        return local_library

    local_signatures = {
        _track_signature(t): i for i, t in enumerate(local_library.snapshot.tracks)
    }

    final_tracks = list(local_library.snapshot.tracks)
    final_sources = list(local_library.sources)

    for item in remote_items:
        sig = _track_signature(item.track)
        match_idx = local_signatures.get(sig)

        if match_idx is not None:
            # Overlap exists
            if policy is OverlapPolicy.PREFER_LOCAL:
                # Keep local copy, skip remote copy
                continue
            if policy is OverlapPolicy.PREFER_JELLYFIN:
                # Replace local copy with remote copy
                final_tracks[match_idx] = item.track
                final_sources[match_idx] = item.source
                continue
            if policy is OverlapPolicy.PROMPT:
                # Keep both as distinct items so SyncDuplicatesDialog resolves them
                final_tracks.append(item.track)
                final_sources.append(item.source)
        else:
            # No overlap: append remote item seamlessly
            final_tracks.append(item.track)
            final_sources.append(item.source)

    combined_snapshot = LibrarySnapshot(
        tracks=tuple(final_tracks),
        playlists=local_library.snapshot.playlists,
        photos=local_library.snapshot.photos,
    )

    return HostMediaLibrary(
        snapshot=combined_snapshot,
        sources=tuple(final_sources),
        issues=local_library.issues,
        cache=local_library.cache,
        artwork_sources=local_library.artwork_sources,
        approved_external_files=local_library.approved_external_files,
        incomplete_playlist_ids=local_library.incomplete_playlist_ids,
        cached_records=local_library.cached_records,
        rechecked_track_paths=local_library.rechecked_track_paths,
        unavailable_artwork_paths=local_library.unavailable_artwork_paths,
    )


def download_jellyfin_track(
    client: JellyfinClient,
    item: JellyfinMediaItem,
    *,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Download a Jellyfin track payload to its local staging cache path."""
    dest = item.cache_path
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest.with_suffix(dest.suffix + ".part")

    # Try direct download first, fallback to stream URL
    url = client.get_download_url(item.item.item_id)
    stream_url = (
        client.get_audio_stream_url(item.item.item_id)
        if item.item.is_audio
        else client.get_video_stream_url(item.item.item_id)
    )

    req = Request(url, headers=client._headers())

    try:
        resp = urlopen(req, timeout=30)
    except HTTPError as exc:
        if exc.code in (401, 403):
            # Fallback to streaming endpoint with embedded api_key
            req = Request(stream_url, headers=client._headers())
            resp = urlopen(req, timeout=30)
        else:
            raise

    total_size = None
    length_header = resp.headers.get("Content-Length")
    if length_header and length_header.isdigit():
        total_size = int(length_header)

    downloaded = 0
    with part_path.open("wb") as out_file:
        while True:
            if checkpoint:
                checkpoint()
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            if progress:
                progress(downloaded, total_size)

    part_path.replace(dest)
    return dest


def download_jellyfin_artwork(
    client: JellyfinClient,
    item_id: str,
    cache_dir: Path,
    *,
    filename: str | None = None,
) -> Path | None:
    """Download an item's primary cover image to cache, returning its path if available."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target_name = filename if filename else f"{item_id}.jpg"
    dest = cache_dir / target_name
    if dest.is_file() and dest.stat().st_size > 0:
        return dest

    headers = client._headers()
    headers["Accept"] = "image/*, */*"

    url = client.get_image_url(item_id)
    req = Request(url, headers=headers)

    ssl_ctx = None
    if url.startswith("https:"):
        try:
            import ssl

            import certifi

            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ssl_ctx = None

    try:
        with urlopen(req, timeout=15, context=ssl_ctx) as resp:
            data = resp.read(16 * 1024 * 1024)
            if data:
                try:
                    with Image.open(BytesIO(data)) as img:
                        img.verify()
                except Exception as verify_err:
                    logger.debug(
                        "Downloaded data for %s is not a valid image: %s",
                        item_id,
                        verify_err,
                    )
                    return None

                part = dest.with_suffix(f".{os.getpid()}_{threading.get_ident()}.part")
                part.write_bytes(data)
                part.replace(dest)
                return dest
    except Exception as exc:
        logger.debug("No artwork available for Jellyfin item %s: %s", item_id, exc)
    return None


class JellyfinArtworkLoader:
    """Lazily decode artwork from Jellyfin items outside the GUI thread."""

    def __init__(self, client: JellyfinClient, cache_dir: Path | None = None) -> None:
        self._client = client
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._targets: dict[int, str] = {}
        self._lock = threading.Lock()

    def register_item(self, item: JellyfinItem) -> None:
        """Associate an item's artwork ID with its image target GUID."""
        art_guid = item.album_id or item.item_id
        if item.image_tag or item.album_primary_image_tag:
            art_id = stable_jellyfin_id(art_guid)
            with self._lock:
                self._targets[art_id] = art_guid

    def register_items(self, items: Iterable[JellyfinItem]) -> None:
        """Register multiple Jellyfin items for lazy artwork resolution."""
        for item in items:
            self.register_item(item)

    def load_artwork(self, request: ArtworkRequest) -> ArtworkImage | None:
        """Fetch, cache, and decode one artwork image outside the GUI thread."""
        dest = self._cache_dir / f"art_{request.artwork_id}.jpg"
        if not (dest.is_file() and dest.stat().st_size > 0):
            with self._lock:
                target_guid = self._targets.get(request.artwork_id)
            if not target_guid:
                return None
            dest_downloaded = download_jellyfin_artwork(
                self._client,
                target_guid,
                self._cache_dir,
                filename=f"art_{request.artwork_id}.jpg",
            )
            if dest_downloaded is None:
                return None
            dest = dest_downloaded

        try:
            with Image.open(dest) as opened:
                oriented = ImageOps.exif_transpose(opened)
                oriented.thumbnail(
                    (request.target_px, request.target_px),
                    Image.Resampling.LANCZOS,
                )
                pixels = oriented.convert("RGB")
                width, height = pixels.size
                rgb888 = pixels.tobytes()
            return ArtworkImage(
                cache_key=f"jellyfin/{request.artwork_id}/{request.target_px}/{width}x{height}",
                artwork_id=request.artwork_id,
                format_id=1,
                width=width,
                height=height,
                rgb888=rgb888,
            )
        except Exception as exc:
            logger.debug(
                "Failed to decode Jellyfin artwork for request %s: %s",
                request.artwork_id,
                exc,
            )
            return None
