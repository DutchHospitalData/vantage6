"""
Unit tests for the temporary-volume and orphaned-resource cleanup logic in
``DockerManager``.

These tests construct a ``DockerManager`` without running its heavy
``__init__`` (which would talk to the Docker daemon). Only the attributes the
methods under test rely on are set up, and the Docker SDK is mocked.
"""

from threading import Lock, Thread
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from docker.errors import APIError, NotFound

from vantage6.node.docker.docker_manager import APPNAME, DockerManager


def _task(vol_name):
    """Build a stand-in for a finished DockerTaskManager."""
    return SimpleNamespace(tmp_vol_name=vol_name)


class TestVolumeRefcount(TestCase):
    """Reference-counted removal of shared temporary volumes."""

    def setUp(self):
        # Build the manager without invoking __init__.
        self.mgr = DockerManager.__new__(DockerManager)
        self.mgr._volume_refcounts = {}
        self.mgr._volume_lock = Lock()
        self.mgr.log = MagicMock()
        self.mgr.docker = MagicMock()
        # Isolate the refcount logic from the real create_volume/Docker.
        self.mgr.create_volume = MagicMock()
        self.volume = MagicMock()
        self.mgr.docker.volumes.get.return_value = self.volume

    def test_reserve_increments_and_creates_volume(self):
        self.mgr._reserve_volume("v1")

        self.assertEqual(self.mgr._volume_refcounts["v1"], 1)
        self.mgr.create_volume.assert_called_once_with("v1")

    def test_single_user_volume_is_removed_on_release(self):
        self.mgr._reserve_volume("v1")

        self.mgr._try_remove_tmp_volume(_task("v1"))

        self.assertNotIn("v1", self.mgr._volume_refcounts)
        self.mgr.docker.volumes.get.assert_called_once_with("v1")
        self.volume.remove.assert_called_once_with()

    def test_shared_volume_removed_only_after_last_release(self):
        # A parent task and its child share one job_id volume.
        self.mgr._reserve_volume("shared")
        self.mgr._reserve_volume("shared")
        self.assertEqual(self.mgr._volume_refcounts["shared"], 2)

        # First task finishes: volume must be kept.
        self.mgr._try_remove_tmp_volume(_task("shared"))
        self.assertEqual(self.mgr._volume_refcounts["shared"], 1)
        self.mgr.docker.volumes.get.assert_not_called()
        self.volume.remove.assert_not_called()

        # Last task finishes: volume is now removed.
        self.mgr._try_remove_tmp_volume(_task("shared"))
        self.assertNotIn("shared", self.mgr._volume_refcounts)
        self.mgr.docker.volumes.get.assert_called_once_with("shared")
        self.volume.remove.assert_called_once_with()

    def test_release_without_volume_name_is_noop(self):
        self.mgr._try_remove_tmp_volume(_task(None))

        self.mgr.docker.volumes.get.assert_not_called()
        self.assertEqual(self.mgr._volume_refcounts, {})

    def test_in_use_volume_is_not_deleted_and_error_is_swallowed(self):
        # Docker refusing removal ("volume in use") is the final backstop that
        # guarantees a working volume is never deleted.
        self.volume.remove.side_effect = APIError("volume is in use")
        self.mgr._reserve_volume("v1")

        # Must not raise.
        self.mgr._try_remove_tmp_volume(_task("v1"))

        self.volume.remove.assert_called_once_with()
        self.assertNotIn("v1", self.mgr._volume_refcounts)

    def test_missing_volume_on_release_is_swallowed(self):
        self.mgr.docker.volumes.get.side_effect = NotFound("no such volume")
        self.mgr._reserve_volume("v1")

        # Must not raise even though the volume is already gone.
        self.mgr._try_remove_tmp_volume(_task("v1"))

        self.assertNotIn("v1", self.mgr._volume_refcounts)

    def test_release_of_unreserved_volume_does_not_go_negative(self):
        self.mgr._try_remove_tmp_volume(_task("never-reserved"))

        # No negative counts should linger in the bookkeeping.
        self.assertNotIn("never-reserved", self.mgr._volume_refcounts)

    def test_concurrent_reserve_release_ends_consistent(self):
        # Each thread does exactly one reserve and one release of the same
        # volume. Because the lock serialises every reserve/release, the final
        # reference count must net to zero regardless of interleaving.
        errors = []

        def _churn():
            try:
                self.mgr._reserve_volume("shared")
                self.mgr._try_remove_tmp_volume(_task("shared"))
            except Exception as exc:  # noqa: BLE001 - record for assertion
                errors.append(exc)

        threads = [Thread(target=_churn) for _ in range(50)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertNotIn("shared", self.mgr._volume_refcounts)


class TestCreateVolume(TestCase):
    """create_volume must be idempotent so reserving a shared volume is safe."""

    def setUp(self):
        self.mgr = DockerManager.__new__(DockerManager)
        self.mgr.log = MagicMock()
        self.mgr.docker = MagicMock()

    def test_creates_volume_when_missing(self):
        self.mgr.docker.volumes.get.side_effect = NotFound("no such volume")

        self.mgr.create_volume("v1")

        self.mgr.docker.volumes.create.assert_called_once_with("v1")

    def test_is_idempotent_when_volume_exists(self):
        self.mgr.docker.volumes.get.return_value = MagicMock()

        self.mgr.create_volume("v1")

        self.mgr.docker.volumes.create.assert_not_called()


class TestRemoveOrphanedVolumes(TestCase):
    """Startup sweep of leftover temporary volumes."""

    def setUp(self):
        self.mgr = DockerManager.__new__(DockerManager)
        self.mgr.log = MagicMock()
        self.mgr.docker = MagicMock()
        self.mgr.ctx = SimpleNamespace(name="aioc-sl-train", scope="user")

    def _volume(self, name, remove_error=None):
        vol = MagicMock()
        vol.name = name
        if remove_error is not None:
            vol.remove.side_effect = remove_error
        return vol

    def test_only_matching_tmp_volumes_are_removed(self):
        prefix = f"{APPNAME}-aioc-sl-train-user-"
        tmp1 = self._volume(f"{prefix}70-tmpvol")
        tmp2 = self._volume(f"{prefix}71-tmpvol")
        data_vol = self._volume(f"{prefix}vol")  # persistent node data
        squid_vol = self._volume(f"{prefix}squid-vol")  # persistent squid data
        other_node = self._volume(f"{APPNAME}-other-user-5-tmpvol")
        self.mgr.docker.volumes.list.return_value = [
            tmp1,
            tmp2,
            data_vol,
            squid_vol,
            other_node,
        ]

        self.mgr._remove_orphaned_volumes()

        tmp1.remove.assert_called_once_with()
        tmp2.remove.assert_called_once_with()
        # Persistent and other-node volumes must never be touched.
        data_vol.remove.assert_not_called()
        squid_vol.remove.assert_not_called()
        other_node.remove.assert_not_called()

    def test_in_use_tmp_volume_is_skipped_without_error(self):
        prefix = f"{APPNAME}-aioc-sl-train-user-"
        in_use = self._volume(f"{prefix}70-tmpvol", remove_error=APIError("in use"))
        self.mgr.docker.volumes.list.return_value = [in_use]

        # Must not raise even though Docker refuses removal.
        self.mgr._remove_orphaned_volumes()

        in_use.remove.assert_called_once_with()


class TestRemoveOrphanedContainers(TestCase):
    """Startup sweep of leftover algorithm/helper containers."""

    def setUp(self):
        self.mgr = DockerManager.__new__(DockerManager)
        self.mgr.log = MagicMock()
        self.mgr.docker = MagicMock()
        self.mgr.node_name = "aioc-sl-train"

    @patch("vantage6.node.docker.docker_manager.remove_container")
    def test_leftover_containers_are_force_removed(self, remove_container):
        c1 = MagicMock()
        c1.name = "vantage6-aioc-sl-train-run-1"
        c2 = MagicMock()
        c2.name = "vantage6-aioc-sl-train-run-1-helper"
        self.mgr.docker.containers.list.return_value = [c1, c2]

        self.mgr._remove_orphaned_containers()

        self.mgr.docker.containers.list.assert_called_once_with(
            all=True,
            filters={"label": ["node=aioc-sl-train"]},
        )
        self.assertEqual(remove_container.call_count, 2)
        remove_container.assert_any_call(c1, kill=True)
        remove_container.assert_any_call(c2, kill=True)

    @patch("vantage6.node.docker.docker_manager.remove_container")
    def test_no_containers_is_noop(self, remove_container):
        self.mgr.docker.containers.list.return_value = []

        self.mgr._remove_orphaned_containers()

        remove_container.assert_not_called()
