
import collections
import os
import sys
import SocketServer
import multiprocessing
import time
import unittest
import mock


def patch_path():
    """
    Add files dir to PYTHONPATH, so we can import sanity_check.py
    """
    THIS_FILE = os.path.abspath(__file__)
    ROOT_DIR = os.path.split(os.path.split(THIS_FILE)[0])[0]

    sys.path.append(os.path.join(ROOT_DIR, 'files'))

patch_path()

import sanity_check

MockStatvfs = collections.namedtuple("MockStatvfs", ['f_bavail', 'f_blocks'])


class UrlopenResponse(object):
    def __init__(self, code):
        super(UrlopenResponse, self).__init__()
        self.code = code

    def getcode(self):
        return self.code


class TestSanityCheck(unittest.TestCase):
    def _make_server_proc(self, host='localhost', port=10123, delay=0):
        def run():
            time.sleep(delay)
            server = SocketServer.TCPServer((host, port), SocketServer.BaseRequestHandler)
            server.serve_forever()
        return run

    def test_port_negative(self):
        # This port is reserved, and in low range so no one should use it
        self.assertFalse(sanity_check.check_port('localhost', 1023))

    def test_port_positive(self):
        # We can assume that SSH will be working w
        server_proc = self._make_server_proc()
        proc = multiprocessing.Process(target=server_proc)
        try:
            proc.start()
            has_connection = False
            for ii in range(100):
                if sanity_check.check_port('localhost', 10123, retries=0, retry_delay=0):
                    has_connection = True
                    time.sleep(.01)
                    break
            self.assertTrue(has_connection)
        finally:
            proc.terminate()

    def test_check_port_retries(self):
        server_proc = self._make_server_proc(delay=2)
        proc = multiprocessing.Process(target=server_proc)
        try:
            proc.start()
            has_connection = sanity_check.check_port('localhost', 10123, retries=3, retry_delay=1)
            self.assertTrue(has_connection)
        finally:
            proc.terminate()

    def test_check_port_retries_negative(self):
        server_proc = self._make_server_proc(delay=2)
        proc = multiprocessing.Process(target=server_proc)
        try:
            proc.start()
            has_connection = sanity_check.check_port('localhost', 10123, retries=-1, retry_delay=1)
            self.assertFalse(has_connection)
        finally:
            proc.terminate()

    def test_command_positive(self):
        self.assertTrue(sanity_check.check_command("echo 1"))

    def test_command_positive_and(self):
        self.assertTrue(sanity_check.check_command("echo 1 && echo 2"))

    def test_command_positive_redirect(self):
        self.assertTrue(sanity_check.check_command("echo foo canary bar | grep canary"))

    def test_command_negative(self):
        self.assertFalse(sanity_check.check_command("exit 1"))

    def test_command_negative_and(self):
        self.assertFalse(sanity_check.check_command("exit 1 && echo 1"))

    def test_command_negative_pipe(self):
        self.assertFalse(sanity_check.check_command("echo foo bar | grep canary"))

    def test_free_percentage(self):
        with mock.patch('os.statvfs') as patched_statvfs:
            patched_statvfs.return_value = MockStatvfs(2, 10)
            self.assertAlmostEqual(20, sanity_check.get_free_percentage_on_mount("/test"))
            patched_statvfs.assert_called_once_with("/test")

    def test_http_checker_positive_http(self):
        self.assertTrue(sanity_check.ping_http_endpoint("http://example.com"))

    def test_http_checker_positive_https(self):
        self.assertTrue(sanity_check.ping_http_endpoint("https://example.com"))

    def test_http_checker_negative_http(self):
        with mock.patch('time.sleep'):
            self.assertFalse(sanity_check.ping_http_endpoint("http://192.168.255.10"))

    def test_http_checker_negative_https(self):
        with mock.patch('time.sleep'):
            self.assertFalse(sanity_check.ping_http_endpoint("https://192.168.255.10"))

    def test_http_checker_statuscode_positive(self):
        with mock.patch('urllib2.urlopen') as patched_open:
            patched_open.return_value = UrlopenResponse(201)
            self.assertTrue(sanity_check.ping_http_endpoint("http://test"))
            patched_open.assert_called_once_with("http://test", timeout=5)

    def test_http_checker_statuscode_negative_100(self):
        with mock.patch('urllib2.urlopen') as patched_open, mock.patch('time.sleep') as patched_sleep:
            patched_open.return_value = UrlopenResponse(102)
            self.assertFalse(sanity_check.ping_http_endpoint("http://test"))
            patched_open.assert_called_with("http://test", timeout=5)
            self.assertEqual(patched_open.call_count, sanity_check.DEFAULT_HTTP_RETRIES)
            self.assertEqual(patched_sleep.call_count, sanity_check.DEFAULT_HTTP_RETRIES)

    def test_http_checker_statuscode_negative_500(self):
        with mock.patch('urllib2.urlopen') as patched_open, mock.patch('time.sleep') as patched_sleep:
            patched_open.return_value = UrlopenResponse(500)
            self.assertFalse(sanity_check.ping_http_endpoint("http://test"))
            patched_open.assert_called_with("http://test", timeout=5)
            self.assertEqual(patched_open.call_count, sanity_check.DEFAULT_HTTP_RETRIES)
            self.assertEqual(patched_sleep.call_count, sanity_check.DEFAULT_HTTP_RETRIES)

    def test_http_checker_retry_and_succeed(self):
        with mock.patch('urllib2.urlopen') as patched_open, mock.patch('time.sleep') as patched_sleep:
            patched_open.side_effect = [UrlopenResponse(500), UrlopenResponse(200)]
            self.assertTrue(sanity_check.ping_http_endpoint("http://test"))
            self.assertEqual(patched_open.call_count, 2)
            self.assertEqual(patched_sleep.call_count, 1)

    def test_get_mounts(self):
        mocked_open = mock.mock_open(
            read_data=self.EXAMPLE_PROC_MOUNTS
        )

        with mock.patch("sanity_check.open", mocked_open):
            mounts = sanity_check.get_dev_mounts()

        self.assertEqual(
            mounts,
            [sanity_check.Mount(mount='/', device='/dev/vda1')]
        )

    def test_global_free_space_percentage(self):

        mounts = [
            sanity_check.Mount(mount='/', device='/dev/vda1'),
            sanity_check.Mount(mount='/var/lib/mysql', device='/dev/vda2')
        ]

        responses = [
            sanity_check.SanityCheckResult(False, "Error on mount"),
            sanity_check.SanityCheckResult(True, "All is OK")
        ]

        report = []

        with mock.patch(
                "sanity_check.test_free_space_on_mount", side_effect=responses
        ) as test_patch, mock.patch(
            "sanity_check.get_dev_mounts", return_value=mounts
        ) as dev_mounts_patch:
            report.extend(sanity_check.test_global_free_percentage(20))

        dev_mounts_patch.assert_called_once_with()
        self.assertEqual(
            test_patch.mock_calls,
            [mock.call('/', 20), mock.call('/var/lib/mysql', 20)]
        )
        self.assertEqual(report, responses)

    EXAMPLE_PROC_MOUNTS = """
sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
udev /dev devtmpfs rw,nosuid,relatime,size=994328k,nr_inodes=248582,mode=755 0 0
devpts /dev/pts devpts rw,nosuid,noexec,relatime,gid=5,mode=620,ptmxmode=000 0 0
tmpfs /run tmpfs rw,nosuid,noexec,relatime,size=199944k,mode=755 0 0
/dev/vda1 / ext4 rw,relatime,data=ordered 0 0
securityfs /sys/kernel/security securityfs rw,nosuid,nodev,noexec,relatime 0 0
tmpfs /dev/shm tmpfs rw,nosuid,nodev 0 0
cgroup /sys/fs/cgroup/systemd cgroup rw,nosuid,nodev,noexec,relatime,xattr,release_agent=/lib/systemd/systemd-cgroups-agent,name=systemd,nsroot=/ 0 0
systemd-1 /proc/sys/fs/binfmt_misc autofs rw,relatime,fd=29,pgrp=1,timeout=0,minproto=5,maxproto=5,direct 0 0
debugfs /sys/kernel/debug debugfs rw,relatime 0 0
mqueue /dev/mqueue mqueue rw,relatime 0 0
hugetlbfs /dev/hugepages hugetlbfs rw,relatime 0 0
fusectl /sys/fs/fuse/connections fusectl rw,relatime 0 0
tmpfs /run/user/1000 tmpfs rw,nosuid,nodev,relatime,size=199944k,mode=700,uid=1000,gid=1000 0 0
    """.strip()


if __name__ == "__main__":
    unittest.main()
