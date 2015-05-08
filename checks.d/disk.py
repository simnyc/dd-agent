# stdlib
import os
import re

# 3p
try:
    import psutil
except ImportError:
    psutil = None

# project
from checks import AgentCheck
from config import _is_affirmative
from util import Platform
import utils.subprocess_output


class Disk(AgentCheck):
    """ Collects metrics about the machine's disks. """
    # -T for filesystem info
    DF_COMMAND = ['df', '-T']
    FAKE_DEVICES = ['udev', 'sysfs', 'rpc_pipefs', 'proc', 'devpts']
    METRIC_DISK = 'system.disk.{0}'
    METRIC_INODE = 'system.fs.inodes.{0}'

    def __init__(self, name, init_config, agentConfig, instances=None):
        if instances is not None and len(instances) > 1:
            raise Exception("Disk check only supports one configured instance.")
        AgentCheck.__init__(self, name, init_config,
                            agentConfig, instances=instances)

    def check(self, instance):
        """Get disk space/inode stats"""
        # First get the configuration.
        self._load_conf(instance)

        if self._psutil():
            self.collect_metrics_psutil()
        else:
            self.collect_metrics_manually()

    def _psutil(self):
        return psutil is not None

    def _load_conf(self, instance):
        self._use_mount = _is_affirmative(instance.get('use_mount', ''))
        self._excluded_filesystems = instance.get('excluded_filesystems', [])
        self._excluded_disks = instance.get('excluded_disks', [])
        self._excluded_disk_re = instance.get('excluded_disk_re', '^$')
        self._tag_by_filesystem = _is_affirmative(
            instance.get('tag_by_filesystem', ''))
        self._all_partitions = _is_affirmative(
            instance.get('all_partitions', 'yes'))

        # FIXME: 6.x, drop device_blacklist_re option in datadog.conf
        device_blacklist_re = self.agentConfig.get('device_blacklist_re')
        if self._excluded_disk_re == '^$' and device_blacklist_re is not None:
            self._excluded_disk_re = device_blacklist_re
        self._excluded_disk_re = re.compile(self._excluded_disk_re)

    def collect_metrics_psutil(self):
        self._valid_disks = {}
        for part in psutil.disk_partitions(all=self._all_partitions):
            # we check all exclude conditions
            if self._exclude_disk_psutil(part):
                continue
            else:
                # For later, latency metrics
                self._valid_disks[part.device] = [part.fstype, part.mountpoint]
            self.log.debug('Passed: {0}'.format(part.device))

            tags = [part.fstype] if self._tag_by_filesystem else []
            device_name = part.mountpoint if self._use_mount else part.device
            for metric_name, metric_value in self._collect_part_metrics(part):
                self.gauge(metric_name, metric_value,
                           tags=tags, device_name=device_name)
        # And finally, latency metrics, a legacy gift from the old Windows Check
        self.collect_latency_metrics()

    def _exclude_disk_psutil(self, part):
        # skip cd-rom drives with no disk in it; they may raise
        # ENOENT, pop-up a Windows GUI error for a non-ready
        # partition or just hang;
        # and all the other excluded disks
        return ((Platform.is_win32() and ('cdrom' in part.opts or
                                          part.fstype == '')) or
                self._exclude_disk(part.device, part.fstype))

    # We don't want all those incorrect devices
    def _exclude_disk(self, name, filesystem):
        return (name in self.FAKE_DEVICES or
                name in self._excluded_disks or
                self._excluded_disk_re.match(name) or
                filesystem in self._excluded_filesystems)

    def _collect_part_metrics(self, part):
        usage = psutil.disk_usage(part.mountpoint)
        metrics = {}
        for name in ['total', 'used', 'free']:
            # For legacy reasons,  the standard unit it kB
            metrics[self.METRIC_DISK.format(name)] = getattr(usage, name) / 1024.0
        # FIXME: 6.x, use percent, a lot more logical than in_use
        metrics[self.METRIC_DISK.format('in_use')] = usage.percent / 100.0
        if Platform.is_unix():
            metrics.update(self._collect_inodes_metrics(part.mountpoint))

        return metrics.iteritems()

    def _collect_inodes_metrics(self, mountpoint):
        metrics = {}
        inodes = os.statvfs(mountpoint)
        if inodes.f_files != 0:
            total = inodes.f_files
            free = inodes.f_ffree
            metrics[self.METRIC_INODE.format('total')] = total
            metrics[self.METRIC_INODE.format('free')] = free
            metrics[self.METRIC_INODE.format('used')] = total - free
            # FIXME: 6.x, use percent, a lot more logical than in_use
            metrics[self.METRIC_INODE.format('in_use')] = \
                (total - free) / float(total)
        return metrics

    def collect_latency_metrics(self):
        for disk_name, disk in psutil.disk_io_counters(True).iteritems():
            # disk_name is sda1, _valid_disks contains /dev/sda1
            match_disks = [d for d in self._valid_disks
                           if re.match('^(/dev/)?{0}$'.format(disk_name), d)]
            if not match_disks:
                continue

            device = match_disks[0]
            fstype, mountpoint = self._valid_disks[device]
            self.log.debug('Passed: {0} -> {1}'.format(disk_name, device))
            tags = []
            if self._tag_by_filesystem:
                tags = [fstype]
            device_name = mountpoint if self._use_mount else device

            # x100 to have it as a percentage,
            # /1000 as psutil returns the value in ms
            read_time_pct = disk.read_time * 100.0 / 1000.0
            write_time_pct = disk.write_time * 100.0 / 1000.0
            self.gauge(self.METRIC_DISK.format('read_time_pct'),
                       read_time_pct, device_name=device_name, tags=tags)
            self.gauge(self.METRIC_DISK.format('write_time_pct'),
                       write_time_pct, device_name=device_name, tags=tags)

    # Y U NO PSUTIL ?!
    def collect_metrics_manually(self):
        df_out = utils.subprocess_output.get_subprocess_output(
            self.DF_COMMAND + ['-k'], self.log
        )
        self.log.debug(df_out)
        for device in self._list_devices(df_out):
            self.log.debug("Passed: {0}".format(device))
            tags = [device[1]] if self._tag_by_filesystem else []
            device_name = device[-1] if self._use_mount else device[0]
            for metric_name, value in self._extract_metrics(device):
                self.gauge(metric_name, value, tags=tags,
                           device_name=device_name)

    def _extract_metrics(self, device):
        result = {}
        # device is
        # ["/dev/sda1", "ext4", 524288,  171642,  352646, "33%", "/"]
        result[self.METRIC_DISK.format('total')] = float(device[2])
        result[self.METRIC_DISK.format('used')] = float(device[3])
        result[self.METRIC_DISK.format('free')] = float(device[4])
        if len(device[5]) > 1 and device[5][-1] == '%':
            result[self.METRIC_DISK.format('in_use')] = \
                float(device[5][:-1]) / 100.0

        result.update(self._collect_inodes_metrics(device[-1]))
        return result.iteritems()

    def _keep_device(self, device):
        # device is for Unix
        # [/dev/disk0s2, ext4, 244277768, 88767396, 155254372, 37%, /]
        # First, skip empty lines.
        # then filter our fake hosts like 'map -hosts'.
        #    Filesystem    Type   1024-blocks     Used Available Capacity  Mounted on
        #    /dev/disk0s2  ext4     244277768 88767396 155254372    37%    /
        #    map -hosts    tmpfs            0        0         0   100%    /net
        # and finally filter out fake devices
        return (device and len(device) > 1 and
                device[2].isdigit() and
                not self._exclude_disk(device[0], device[1]))

    def _flatten_devices(self, devices):
        # Some volumes are stored on their own line. Rejoin them here.
        previous = None
        for parts in devices:
            if len(parts) == 1:
                previous = parts[0]
            elif previous and self._is_number(parts[0]):
                # collate with previous line
                parts.insert(0, previous)
                previous = None
            else:
                previous = None
        return devices

    def _list_devices(self, df_output):
        """
        Given raw output for the df command, transform it into a normalized
        list devices. A 'device' is a list with fields corresponding to the
        output of df output on each platform.
        """
        all_devices = [l.strip().split() for l in df_output.split("\n")]

        # Skip the header row and empty lines.
        raw_devices = [l for l in all_devices[1:] if l]

        # Flatten the disks that appear in the mulitple lines.
        flattened_devices = self._flatten_devices(raw_devices)

        # Filter fake or unwanteddisks.
        return [d for d in flattened_devices if self._keep_device(d)]
