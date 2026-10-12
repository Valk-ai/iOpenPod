"""Select All and Deselect All navigation actions toggle host media selection."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QLabel, QPushButton
from tests.iOpenPod.app.services.test_library_resources import build_device
from tests.iOpenPod.GUI.application_shell_test_support import APPLICATION, build_context

from iOpenPod.app.host_media_library import (
    HostMediaCacheStats,
    HostMediaFileKind,
    HostMediaLibrary,
    HostMediaSource,
)
from iOpenPod.app.library_sync_helper import (
    IPodMediaCacheStats,
    IPodMediaLibrary,
    IPodTrackFingerprint,
)
from iOpenPod.app.sync_plan import SyncPlan, prepare_sync_plan
from iOpenPod.GUI.presentation.artwork_provider import ArtworkPixmapProvider
from iOpenPod.GUI.sync_workspace import SyncWorkspace
from iPodDB.library import (
    LibrarySnapshot,
    MediaType,
    Photo,
    PhotoLibrary,
    PhotoRepresentation,
    PhotoRepresentationKind,
    Track,
    TrackMetadata,
)
from storage import DevicePath, HostPath


@pytest.fixture
def workspace() -> Iterator[SyncWorkspace]:
    context = build_context()
    artwork = ArtworkPixmapProvider(context.artwork_controller)
    page = SyncWorkspace(context.settings, context.theme_manager, artwork)
    page.resize(1050, 800)
    page.show()
    try:
        yield page
    finally:
        page.shutdown()
        artwork.shutdown()
        page.close()
        page.deleteLater()
        APPLICATION.sendPostedEvents(page, QEvent.Type.DeferredDelete)
        context.shutdown()


def test_sync_heading_bar_select_all_and_deselect_all_buttons_exist_and_labeled(
    tmp_path: Path,
    workspace: SyncWorkspace,
) -> None:
    device = build_device(tmp_path)
    try:
        host = HostMediaLibrary(LibrarySnapshot(), (), (), HostMediaCacheStats())
        comparison = SyncPlan(())
        ipod_media = IPodMediaLibrary((), (), (), IPodMediaCacheStats(), None, False)
        workspace.load_comparison(host, comparison, device.active, ipod_media)
        workspace.show_selection()

        select_all = workspace.findChild(QPushButton, "syncSelectAll")
        deselect_all = workspace.findChild(QPushButton, "syncDeselectAll")

        assert select_all is not None
        assert deselect_all is not None
        assert select_all.isVisible()
        assert deselect_all.isVisible()
        assert select_all.text() == "Select All"
        assert deselect_all.text() == "Deselect All"
        assert "Select all" in select_all.toolTip()
        assert "Deselect all" in deselect_all.toolTip()
        assert select_all.accessibleName() == "Select all Host media"
        assert deselect_all.accessibleName() == "Deselect all Host media"

        # Switch to Tracks page: tracks heading bar also has bulk selection buttons
        workspace._show_library_page("tracks")
        tracks_select_all = workspace.findChild(QPushButton, "tracksSelectAll")
        tracks_deselect_all = workspace.findChild(QPushButton, "tracksDeselectAll")
        assert tracks_select_all is not None
        assert tracks_deselect_all is not None
        assert tracks_select_all.isVisible()
        assert tracks_deselect_all.isVisible()
    finally:
        device.coordinator.close()


def test_sync_workspace_bulk_selection_updates_models_and_summary(
    tmp_path: Path,
    workspace: SyncWorkspace,
) -> None:
    device = build_device(tmp_path)
    try:
        paths = tuple(tmp_path / f"song-{i}.mp3" for i in (1, 2, 3))
        tracks = tuple(
            Track(
                i,
                f"Song {i}",
                "Artist",
                "Album",
                180_000,
                metadata=TrackMetadata(location=str(path)),
            )
            for i, path in enumerate(paths, 1)
        )
        host = HostMediaLibrary(
            LibrarySnapshot(tracks=tracks),
            tuple(
                HostMediaSource(
                    HostPath(path),
                    HostMediaFileKind.AUDIO,
                    100,
                    1000,
                    acoustic_fingerprint=f"{i},{i + 1}",
                )
                for i, path in enumerate(paths, 1)
            ),
            (),
            HostMediaCacheStats(),
        )
        ipod_tracks = (
            IPodTrackFingerprint(
                1001,
                1,
                DevicePath("iPod_Control/Music/F00/1.mp3"),
                100,
                1000,
                "1,2",
            ),
        )
        ipod_media = IPodMediaLibrary(
            ipod_tracks,
            (),
            (),
            IPodMediaCacheStats(),
            None,
            False,
        )
        ipod_snapshot = LibrarySnapshot(tracks=(tracks[0],))
        comparison = prepare_sync_plan(host, ipod_media, ipod_snapshot)

        workspace.load_comparison(host, comparison, device.active, ipod_media)
        workspace.show_selection()

        select_all = workspace.findChild(QPushButton, "syncSelectAll")
        deselect_all = workspace.findChild(QPushButton, "syncDeselectAll")
        summary = workspace.findChild(QLabel, "syncSelectionSummary")
        assert (
            select_all is not None and deselect_all is not None and summary is not None
        )

        # Initially, track 1 is matched and selected by default
        assert workspace.selection.selected_host_count == 1
        assert "1 Host item selected" in summary.text()

        # Click Select All: all 3 tracks become selected
        select_all.click()
        assert workspace.selection.selected_host_count == 3
        assert workspace.selection.track_check_state(1) is Qt.CheckState.Checked
        assert workspace.selection.track_check_state(2) is Qt.CheckState.Checked
        assert workspace.selection.track_check_state(3) is Qt.CheckState.Checked
        assert "3 Host items selected" in summary.text()

        # Click Deselect All: all tracks become deselected
        deselect_all.click()
        assert workspace.selection.selected_host_count == 0
        assert workspace.selection.track_check_state(1) is Qt.CheckState.Unchecked
        assert workspace.selection.track_check_state(2) is Qt.CheckState.Unchecked
        assert workspace.selection.track_check_state(3) is Qt.CheckState.Unchecked
        assert "0 Host items selected" in summary.text()

        # Pass-through methods on workspace
        assert workspace.select_all_host()
        assert workspace.selection.selected_host_count == 3
        assert workspace.deselect_all_host()
        assert workspace.selection.selected_host_count == 0
    finally:
        device.coordinator.close()


def test_bulk_selection_scoped_to_active_tab(
    tmp_path: Path,
    workspace: SyncWorkspace,
) -> None:
    device = build_device(tmp_path)
    try:
        music_path_1 = tmp_path / "song-1.mp3"
        music_path_2 = tmp_path / "song-2.mp3"
        podcast_path = tmp_path / "cast.mp3"
        photo_path = tmp_path / "photo.jpg"

        tracks = (
            Track(
                1,
                "Song 1",
                "Artist A",
                "Album A",
                180_000,
                metadata=TrackMetadata(location=str(music_path_1)),
            ),
            Track(
                2,
                "Song 2",
                "Artist B",
                "Album B",
                180_000,
                metadata=TrackMetadata(location=str(music_path_2)),
            ),
            Track(
                3,
                "Episode 1",
                "Cast Artist",
                "Podcast Album",
                180_000,
                media_types=(MediaType.PODCAST,),
                metadata=TrackMetadata(location=str(podcast_path)),
            ),
        )
        photos = PhotoLibrary(
            photos=(
                Photo(
                    1,
                    representations=(
                        PhotoRepresentation(
                            PhotoRepresentationKind.FULL_RESOLUTION,
                            1,
                            str(photo_path),
                            0,
                            100,
                            10,
                            10,
                        ),
                    ),
                ),
            )
        )
        host = HostMediaLibrary(
            LibrarySnapshot(tracks=tracks, photos=photos),
            tuple(
                HostMediaSource(
                    HostPath(p),
                    HostMediaFileKind.AUDIO,
                    100,
                    1000,
                    acoustic_fingerprint=f"{i},{i + 1}",
                )
                for i, p in enumerate((music_path_1, music_path_2, podcast_path), 1)
            ),
            (),
            HostMediaCacheStats(),
        )
        ipod_media = IPodMediaLibrary((), (), (), IPodMediaCacheStats(), None, False)
        comparison = prepare_sync_plan(host, ipod_media, LibrarySnapshot())

        workspace.load_comparison(host, comparison, device.active, ipod_media)
        workspace.show_selection()

        # Initial state: no items checked
        assert workspace.selection.selected_host_count == 0

        # We start on Albums page (default):
        albums_select_all = workspace.findChild(QPushButton, "syncSelectAll")
        albums_deselect_all = workspace.findChild(QPushButton, "syncDeselectAll")
        assert albums_select_all is not None and albums_deselect_all is not None

        # Click Select All on Albums: only tracks 1 and 2 (the music albums) are checked
        albums_select_all.click()
        assert workspace.selection.track_check_state(1) is Qt.CheckState.Checked
        assert workspace.selection.track_check_state(2) is Qt.CheckState.Checked
        assert workspace.selection.track_check_state(3) is Qt.CheckState.Unchecked
        assert workspace.selection.photo_check_state(1) is Qt.CheckState.Unchecked
        assert workspace.selection.selected_host_count == 2

        # Switch to Podcasts tab
        workspace._show_library_page("podcasts")
        podcasts_select_all = workspace.findChild(QPushButton, "podcastsSelectAll")
        podcasts_deselect_all = workspace.findChild(QPushButton, "podcastsDeselectAll")
        assert podcasts_select_all is not None and podcasts_deselect_all is not None

        # Click Select All on Podcasts: podcast track 3 is now checked as well
        podcasts_select_all.click()
        assert workspace.selection.track_check_state(1) is Qt.CheckState.Checked
        assert workspace.selection.track_check_state(2) is Qt.CheckState.Checked
        assert workspace.selection.track_check_state(3) is Qt.CheckState.Checked
        assert workspace.selection.photo_check_state(1) is Qt.CheckState.Unchecked
        assert workspace.selection.selected_host_count == 3

        # Click Deselect All on Podcasts: only podcast track 3 is deselected
        podcasts_deselect_all.click()
        assert workspace.selection.track_check_state(1) is Qt.CheckState.Checked
        assert workspace.selection.track_check_state(2) is Qt.CheckState.Checked
        assert workspace.selection.track_check_state(3) is Qt.CheckState.Unchecked
        assert workspace.selection.photo_check_state(1) is Qt.CheckState.Unchecked
        assert workspace.selection.selected_host_count == 2

        # Switch to Photos tab
        workspace._show_library_page("photos")
        photos_select_all = workspace.findChild(QPushButton, "photosSelectAll")
        photos_deselect_all = workspace.findChild(QPushButton, "photosDeselectAll")
        assert photos_select_all is not None and photos_deselect_all is not None

        # Click Select All on Photos: photo 1 is now checked
        photos_select_all.click()
        assert workspace.selection.photo_check_state(1) is Qt.CheckState.Checked
        assert workspace.selection.selected_host_count == 3

        # Click Deselect All on Photos: photo 1 is deselected
        photos_deselect_all.click()
        assert workspace.selection.photo_check_state(1) is Qt.CheckState.Unchecked
        assert workspace.selection.selected_host_count == 2

        # Switch back to Albums tab and Deselect All: tracks 1 and 2 are deselected
        workspace._show_library_page("albums")
        albums_deselect_all.click()
        assert workspace.selection.track_check_state(1) is Qt.CheckState.Unchecked
        assert workspace.selection.track_check_state(2) is Qt.CheckState.Unchecked
        assert workspace.selection.selected_host_count == 0
    finally:
        device.coordinator.close()
