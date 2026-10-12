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
from iPodDB.library import LibrarySnapshot, Track, TrackMetadata
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
