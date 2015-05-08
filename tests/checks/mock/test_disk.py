# stdlib
import mock

# project
from tests.checks.common import AgentCheckTest, Fixtures


DEFAULT_DEVICE_NAME = '/dev/sda1'


class MockPart(object):
    def __init__(self, device=DEFAULT_DEVICE_NAME, fstype='ext4',
                 mountpoint='/'):
        self.device = device
        self.fstype = fstype
        self.mountpoint = mountpoint


class MockDiskMetrics(object):
    def __init__(self):
        self.total = 5 * 1024
        self.used = 4 * 1024
        self.free = 1 * 1024
        self.percent = 80


class MockInodesMetrics(object):
    def __init__(self):
        self.f_files = 10
        self.f_ffree = 9


class MockIoCountersMetrics(object):
    def __init__(self):
        self.read_time = 15
        self.write_time = 25


class TestCheckDisk(AgentCheckTest):
    CHECK_NAME = 'disk'

    GAUGES_VALUES = {
        'system.disk.total': 5,
        'system.disk.used': 4,
        'system.disk.free': 1,
        'system.disk.in_use': .80,
        'system.fs.inodes.total': 10,
        'system.fs.inodes.used': 1,
        'system.fs.inodes.free': 9,
        'system.fs.inodes.in_use': .10
    }

    GAUGES_VALUES_PSUTIL = {
        'system.disk.read_time_pct': 1.5,
        'system.disk.write_time_pct': 2.5,
    }

    def test_device_exclusion_logic(self):
        self.run_check({'instances': [{'use_mount': 'no',
                                       'excluded_filesystems': ['aaaaaa'],
                                       'excluded_disks': ['bbbbbb'],
                                       'excluded_disk_re': '^tev+$'}]},
                       mocks={'collect_metrics': lambda: None})
        # should pass, default mock is a normal disk
        exclude_disk = self.check._exclude_disk_psutil
        self.assertFalse(exclude_disk(MockPart()))

        # standard fake devices
        self.assertTrue(exclude_disk(MockPart(device='udev')))

        # excluded filesystems list
        self.assertTrue(exclude_disk(MockPart(fstype='aaaaaa')))
        self.assertFalse(exclude_disk(MockPart(fstype='a')))

        # excluded devices list
        self.assertTrue(exclude_disk(MockPart(device='bbbbbb')))
        self.assertFalse(exclude_disk(MockPart(device='b')))

        # excluded devices regex
        self.assertTrue(exclude_disk(MockPart(device='tevvv')))
        self.assertFalse(exclude_disk(MockPart(device='tevvs')))

    @mock.patch('psutil.disk_partitions', return_value=[MockPart()])
    @mock.patch('psutil.disk_usage', return_value=MockDiskMetrics())
    @mock.patch('psutil.disk_io_counters',
                return_value={'sda1': MockIoCountersMetrics()})
    @mock.patch('os.statvfs', return_value=MockInodesMetrics())
    def test_psutil(self, mock_partitions, mock_usage,
                    mock_io_counters, mock_inodes):
        for tag_by in ['yes', 'no']:
            self.run_check({'instances': [{'tag_by_filesystem': tag_by}]},
                           force_reload=True)

            # Assert metrics
            tags = ['ext4'] if tag_by == 'yes' else []
            self.GAUGES_VALUES_PSUTIL.update(self.GAUGES_VALUES)
            for metric, value in self.GAUGES_VALUES_PSUTIL.iteritems():
                self.assertMetric(metric, value=value, tags=tags,
                                  device_name=DEFAULT_DEVICE_NAME)

            self.coverage_report()

    @mock.patch('psutil.disk_partitions', return_value=[MockPart()])
    @mock.patch('psutil.disk_usage', return_value=MockDiskMetrics())
    @mock.patch('psutil.disk_io_counters',
                return_value={'sda1': MockIoCountersMetrics()})
    @mock.patch('os.statvfs', return_value=MockInodesMetrics())
    def test_use_mount(self, mock_partitions, mock_usage,
                       mock_io_counters, mock_inodes):
        self.run_check({'instances': [{'use_mount': 'yes'}]})

        # Assert metrics
        self.GAUGES_VALUES_PSUTIL.update(self.GAUGES_VALUES)
        for metric, value in self.GAUGES_VALUES_PSUTIL.iteritems():
            self.assertMetric(metric, value=value, tags=[],
                              device_name='/')

        self.coverage_report()

    @mock.patch('utils.subprocess_output.get_subprocess_output',
                return_value=Fixtures.read_file('debian-df-Tk'))
    @mock.patch('os.statvfs', return_value=MockInodesMetrics())
    def test_no_psutil_debian(self, mock_df_output, mock_statvfs):
        self.run_check({'instances': [{'use_mount': 'no',
                                       'excluded_filesystems': ['tmpfs']}]},
                       mocks={'_psutil': lambda: False})

        for metric, value in self.GAUGES_VALUES.iteritems():
            self.assertMetric(metric, value=value, tags=[],
                              device_name=DEFAULT_DEVICE_NAME)

        self.coverage_report()

    @mock.patch('utils.subprocess_output.get_subprocess_output',
                return_value=Fixtures.read_file('freebsd-df-Tk'))
    @mock.patch('os.statvfs', return_value=MockInodesMetrics())
    def test_no_psutil_freebsd(self, mock_df_output, mock_statvfs):
        self.run_check({'instances': [{'use_mount': 'no',
                                       'excluded_filesystems': ['devfs'],
                                       'excluded_disk_re': 'zroot/.+'}]},
                       mocks={'_psutil': lambda: False})

        for metric, value in self.GAUGES_VALUES.iteritems():
            self.assertMetric(metric, value=value, tags=[],
                              device_name='zroot')

        self.coverage_report()
