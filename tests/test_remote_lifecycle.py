import sys
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'launcher'))
import remote


class RemoteLifecycleTests(unittest.TestCase):
    def manager(self):
        manager=remote.RemoteBotManager.__new__(remote.RemoteBotManager)
        manager.service='im-bridge'
        manager._auto_restart=False
        manager._host_cache=None
        manager.log=Mock()
        manager._systemctl=Mock()
        return manager

    @patch.object(remote,'run',return_value=(0,'active|enabled|123|42\n'))
    def test_status_exposes_running_at_top_level(self,run):
        status=self.manager().status(force=True)
        self.assertTrue(status['running'])
        self.assertEqual(status['pid'],123)
        self.assertNotIn('status',status)

    @patch.object(remote,'run',side_effect=remote.RemoteError('unreachable'))
    def test_failed_probe_retains_flat_status(self,run):
        status=self.manager().status(force=True)
        self.assertFalse(status['running'])
        self.assertIn('unreachable',status['error'])

    @patch.object(remote.time,'sleep')
    @patch.object(remote,'run',return_value=(0,'active|enabled|123|42\n'))
    def test_restart_accepts_active_service(self,run,sleep):
        manager=self.manager()
        result=manager.restart()
        manager._systemctl.assert_called_once_with('restart')
        self.assertTrue(result['running'])

    @patch.object(remote.time,'sleep')
    @patch.object(remote,'run',return_value=(0,'inactive|disabled|0|\n'))
    def test_restart_rejects_inactive_service(self,run,sleep):
        with self.assertRaises(remote.RemoteError):self.manager().restart()

    @patch.object(remote,'run',return_value=(0,'active|enabled|123|42\n'))
    def test_start_leaves_existing_service_running(self,run):
        manager=self.manager()
        self.assertTrue(manager.start()['already'])
        manager._systemctl.assert_not_called()
