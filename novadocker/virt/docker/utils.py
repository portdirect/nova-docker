# Copyright (c) 2013 dotCloud, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import os
import errno
import shutil


from oslo_concurrency import processutils
from oslo_log import log as logging
from oslo_utils import fileutils
from oslo_utils import units

import nova.conf
from nova.i18n import _
from nova.i18n import _LW
from nova import utils
from nova import version
from nova.virt import volumeutils

LOG = logging.getLogger(__name__)

CONF = nova.conf.CONF

CONFIGDRIVESIZE_BYTES = 64 * units.Mi

def execute(*args, **kwargs):
    return utils.execute(*args, **kwargs)


def get_iscsi_initiator():
    return volumeutils.get_iscsi_initiator()


def get_fc_hbas():
    """Get the Fibre Channel HBA information."""
    out = None
    try:
        out, err = execute('systool', '-c', 'fc_host', '-v',
                           run_as_root=True)
    except processutils.ProcessExecutionError as exc:
        # This handles the case where rootwrap is used
        # and systool is not installed
        # 96 = nova.cmd.rootwrap.RC_NOEXECFOUND:
        if exc.exit_code == 96:
            LOG.warn(_LW("systool is not installed"))
        return []
    except OSError as exc:
        # This handles the case where rootwrap is NOT used
        # and systool is not installed
        if exc.errno == errno.ENOENT:
            LOG.warn(_LW("systool is not installed"))
        return []

    if out is None:
        raise RuntimeError(_("Cannot find any Fibre Channel HBAs"))

    lines = out.split('\n')
    # ignore the first 2 lines
    lines = lines[2:]
    hbas = []
    hba = {}
    lastline = None
    for line in lines:
        line = line.strip()
        # 2 newlines denotes a new hba port
        if line == '' and lastline == '':
            if len(hba) > 0:
                hbas.append(hba)
                hba = {}
        else:
            val = line.split('=')
            if len(val) == 2:
                key = val[0].strip().replace(" ", "")
                value = val[1].strip()
                hba[key] = value.replace('"', '')
        lastline = line

    return hbas


def get_fc_wwpns():
    """Get Fibre Channel WWPNs from the system, if any."""
    # Note modern linux kernels contain the FC HBA's in /sys
    # and are obtainable via the systool app
    hbas = get_fc_hbas()

    wwpns = []
    if hbas:
        for hba in hbas:
            if hba['port_state'] == 'Online':
                wwpn = hba['port_name'].replace('0x', '')
                wwpns.append(wwpn)

    return wwpns


def get_fc_wwnns():
    """Get Fibre Channel WWNNs from the system, if any."""
    # Note modern linux kernels contain the FC HBA's in /sys
    # and are obtainable via the systool app
    hbas = get_fc_hbas()

    wwnns = []
    if hbas:
        for hba in hbas:
            if hba['port_state'] == 'Online':
                wwnn = hba['node_name'].replace('0x', '')
                wwnns.append(wwnn)

    return wwnns


class ConfigDriveBuilder(object):
    """Build config drives, optionally as a context manager."""

    def __init__(self, instance_md=None):
        self.imagefile = None
        self.mdfiles = []

        if instance_md is not None:
            self.add_instance_metadata(instance_md)

    def __enter__(self):
        return self

    def __exit__(self, exctype, excval, exctb):
        if exctype is not None:
            # NOTE(mikal): this means we're being cleaned up because an
            # exception was thrown. All bets are off now, and we should not
            # swallow the exception
            return False
        self.cleanup()

    def _add_file(self, basedir, path, data):
        filepath = os.path.join(basedir, path)
        dirname = os.path.dirname(filepath)
        fileutils.ensure_tree(dirname)
        with open(filepath, 'wb') as f:
            f.write(data)

    def add_instance_metadata(self, instance_md):
        for (path, data) in instance_md.metadata_for_config_drive():
            self.mdfiles.append((path, data))

    def _write_md_files(self, basedir):
        for data in self.mdfiles:
            self._add_file(basedir, data[0], data[1])

    def make_drive(self, path):
        """Make the config drive.
        :param path: the path to place the config drive image at
        :raises ProcessExecuteError if a helper process has failed.
        """
        self._write_md_files(path)

    def cleanup(self):
        if self.imagefile:
            fileutils.delete_if_exists(self.imagefile)

    def __repr__(self):
        return "<ConfigDriveBuilder: " + str(self.mdfiles) + ">"
