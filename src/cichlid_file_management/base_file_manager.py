"""Shared rclone-backed transfer engine for the McGrath lab pipelines.

`BaseFileManager` owns the part every repo relies on -- downloads, uploads,
cloud listing/existence/size/delete -- built on a single hardened rclone wrapper
with retries and exit-code awareness. Each repo subclasses it and adds only its
own paths and domain methods, so the transfer surface has exactly one
implementation instead of one per repo.

Subclasses must implement `_registerPaths`, which runs during __init__ after the
master directories are configured and before any subclass-specific
authentication or data setup.
"""

import json
import logging
import os
import platform
import subprocess
import time
from abc import ABC, abstractmethod
from types import SimpleNamespace
import inspect

logger = logging.getLogger(__name__)

# rclone exit codes (see https://rclone.org/docs/#exit-code).
# Retrying these will not help, so we stop immediately rather than backing off.
_RCLONE_NOT_FOUND = frozenset({3, 4})  # directory / file not found
_RCLONE_FATAL = frozenset({1, 7})      # usage error / fatal (e.g. auth, account)


class BaseFileManager(ABC):
    # The shared Dropbox "Apps" directory every project's cloud data lives under;
    # the per-repo project string is appended to this. Override on a subclass only
    # if a repo's cloud data lives somewhere else.
    _CLOUD_APPS_DIR = 'COS/BioSci/BioSci-McGrath/Apps/'
    # utaka local storage root (the per-user Temp dir sits under this).
    _UTAKA_STORAGE = '/Data'
    # Mount point scanned for the writable data drive on a Raspberry Pi.
    _PI_MOUNT = '/media/pi/'

    def __init__(self, projectData, rcloneRemote='ptm_dropbox:/'):
        # projectData is the single per-repo name (e.g. 'CichlidMorphometricData')
        # appended to both the cloud and local master directories.
        self.rcloneRemote = rcloneRemote
        self.projectData = projectData.strip('/')
        suffix = '/'.join(p for p in (self._CLOUD_APPS_DIR.strip('/'), self.projectData) if p)
        self.cloudMasterDir = rcloneRemote.rstrip('/') + '/' + suffix + '/'
        self.localMasterDir = self._identifyLocalMasterDir(self.projectData)
        self._registerPaths()

    @abstractmethod
    def _registerPaths(self):
        """Set repo-specific local/cloud paths.

        Called during __init__ once the master directories are configured and
        before any subclass authentication or data setup. A subclass cannot be
        instantiated without defining this.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Environment detection                                              #
    # ------------------------------------------------------------------ #

    def _identifyLocalMasterDir(self, projectData):
        # Resolve the local master directory for the current machine and record
        # the machine kind on self.system ('pi' | 'utaka' | 'other').
        if self._isRaspberryPi():
            self.system = 'pi'
            return self._piLocalBase() + projectData + '/'
        if 'utaka' in platform.node():
            self.system = 'utaka'
            return self._UTAKA_STORAGE.rstrip('/') + '/' + os.getenv('USER') + '/Temp/' + projectData + '/'
        self.system = 'other'
        return os.getenv('HOME').rstrip('/') + '/Temp/' + projectData + '/'
    def _detect_branch_name(self):
        """Git branch of the repo defining the concrete FileManager subclass.

        Uses the subclass's source location (not the CWD), so each repo records its
        own branch regardless of where the process was launched. Falls back
        gracefully when git or a .git dir isn't present (e.g. a non-editable install).
        """
        try:
            repo_dir = os.path.dirname(os.path.abspath(inspect.getfile(type(self))))
            out = subprocess.run(
                ["git", "-C", repo_dir, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True,
            )
            branch = out.stdout.strip()
            if out.returncode != 0 or not branch:
                return "unknown"
            if branch == "HEAD":  # detached checkout -> record the short commit
                sha = subprocess.run(
                    ["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True,
                ).stdout.strip()
                return f"detached@{sha}" if sha else "unknown"
            return branch
        except (FileNotFoundError, OSError, TypeError):
            return "unknown"
            
    @staticmethod
    def _isRaspberryPi():
        node = platform.node()
        if node == 'raspberrypi' or 'Pi' in node or 'bt-' in node or 'sv-' in node:
            return True
        # Hardware fallback: the device tree names the board on a real Pi.
        try:
            with open('/sys/firmware/devicetree/base/model') as f:
                return 'raspberry pi' in f.read().lower()
        except OSError:
            return False

    def _piLocalBase(self):
        # Find the single writable drive under the Pi mount point and return it.
        mount = self._PI_MOUNT
        try:
            candidates = os.listdir(mount)
        except FileNotFoundError:
            raise FileNotFoundError('Expected Raspberry Pi mount point {!r} not found'.format(mount))
        writable = []
        for d in candidates:
            probe = os.path.join(mount, d, '.cfm_write_test')
            try:
                with open(probe, 'w') as handle:
                    handle.write('test')
                os.remove(probe)
                writable.append(d)
            except OSError:
                pass
        if len(writable) == 1:
            return os.path.join(mount, writable[0]) + '/'
        if not writable:
            raise RuntimeError('No writable drive found under ' + mount)
        raise RuntimeError('Multiple writable drives under {}: {}'.format(mount, sorted(writable)))

    # ------------------------------------------------------------------ #
    # Path mapping                                                        #
    # ------------------------------------------------------------------ #

    def _cloudDir(self, local_dir):
        # Map a local directory to its cloud counterpart by swapping the
        # master-dir prefix. Anchored to the prefix so it cannot match or replace
        # mid-path, tolerant of local_dir == localMasterDir (e.g. listing the
        # master root), and raises if the path is not under the master dir rather
        # than silently returning a garbled cloud path. Returns a trailing slash.
        local_dir = local_dir.rstrip('/')
        master = self.localMasterDir.rstrip('/')
        if local_dir != master and not local_dir.startswith(master + '/'):
            raise ValueError(
                'Local path {!r} is not under localMasterDir {!r}'.format(local_dir, self.localMasterDir)
            )
        suffix = local_dir[len(master):].lstrip('/')
        cloud = self.cloudMasterDir.rstrip('/')
        if suffix:
            cloud = cloud + '/' + suffix
        return cloud + '/'

    def _localToCloud(self, local_data, tarred=False):
        # Resolve every path and name a transfer needs from one local path, and
        # return them as a namespace: local_data (cleaned), base (leaf without
        # .tar), relative_name (leaf moved, base or base + '.tar'), parent_dir,
        # cloud_parent (trailing slash), cloud_target, local_target.
        local_data = local_data.rstrip('/')
        base = os.path.basename(local_data)
        parent_dir = os.path.dirname(local_data)
        relative_name = base + '.tar' if tarred else base
        cloud_parent = self._cloudDir(parent_dir)

        return SimpleNamespace(
            local_data=local_data,
            base=base,
            relative_name=relative_name,
            parent_dir=parent_dir,
            cloud_parent=cloud_parent,
            cloud_target=cloud_parent + relative_name,
            local_target=os.path.join(parent_dir, relative_name),
        )

    # ------------------------------------------------------------------ #
    # rclone wrapper                                                      #
    # ------------------------------------------------------------------ #

    def _run_rclone(self, args, retries=3, backoff=2.0, allow_codes=()):
        # Run an rclone command, retrying on transient failures with a linear
        # backoff. Not-found and fatal exit codes break immediately because
        # retrying cannot help. The final completed process is returned so the
        # caller can branch on its return code; allow_codes are returned without
        # being treated as failures.
        proc = None
        for attempt in range(1, retries + 1):
            proc = subprocess.run(['rclone'] + list(args), capture_output=True, encoding='utf-8')
            if proc.returncode == 0 or proc.returncode in allow_codes:
                return proc
            if proc.returncode in _RCLONE_NOT_FOUND or proc.returncode in _RCLONE_FATAL:
                break
            logger.warning(
                'rclone %s failed (exit %s), attempt %d/%d: %s',
                args[0] if args else '', proc.returncode, attempt, retries, proc.stderr.strip(),
            )
            if attempt < retries:
                time.sleep(backoff * attempt)
        return proc

    # ------------------------------------------------------------------ #
    # Transfers                                                           #
    # ------------------------------------------------------------------ #

    def downloadData(self, local_data, tarred=False, tarred_subdirs=False,
                     allow_errors=False, quiet=False, parallel=False, multi_thread=False):
        # parallel=True returns the rclone command (a list) for the caller to run
        # however it manages concurrency, and skips the inline post-steps
        # (existence check, extraction). multi_thread=True adds rclone's
        # multi-thread streaming for large single files.
        if local_data is None:
            return

        if parallel and (tarred or tarred_subdirs):
            raise ValueError(
                'parallel downloads cannot extract archives inline; run without '
                'parallel, or extract after the transfer the caller launches completes'
            )

        p = self._localToCloud(local_data, tarred=tarred)

        def _handle(message, error_cls):
            if allow_errors:
                if not quiet:
                    logger.warning('%s. Continuing.', message)
                return True
            raise error_cls(message)

        listing = self._run_rclone(['lsf', p.cloud_parent], allow_codes=_RCLONE_NOT_FOUND)
        if listing.returncode not in _RCLONE_NOT_FOUND and listing.returncode != 0:
            raise IOError(
                'rclone could not list {} (exit {}): {}'.format(
                    p.cloud_parent, listing.returncode, listing.stderr.strip()
                )
            )
        cloud_objects = listing.stdout.split() if listing.returncode == 0 else []

        is_dir = p.relative_name + '/' in cloud_objects
        is_file = p.relative_name in cloud_objects
        if not (is_dir or is_file):
            _handle('Cannot find file for download: ' + p.cloud_target, FileNotFoundError)
            return

        flags = ['--multi-thread-streams', '96', '--multi-thread-cutoff', '100Mi'] if multi_thread else []
        dest = p.local_target if is_dir else p.parent_dir
        cmd = ['copy'] + flags + [p.cloud_target, dest]

        if parallel:
            return ['rclone'] + cmd

        result = self._run_rclone(cmd)
        if result.returncode != 0:
            if _handle(
                'Error downloading {} (rclone exit {}): {}'.format(
                    p.cloud_target, result.returncode, result.stderr.strip()
                ),
                IOError,
            ):
                return

        if not os.path.exists(p.local_target):
            if _handle('Download reported success but {} is missing'.format(p.local_target), FileNotFoundError):
                return

        if tarred:
            untar = subprocess.run(['tar', '-xf', p.local_target, '-C', p.parent_dir], capture_output=True, encoding='utf-8')
            if untar.returncode != 0:
                raise IOError('Failed to extract {}: {}'.format(p.local_target, untar.stderr.strip()))
            os.remove(p.local_target)

        if tarred_subdirs:
            for d in [x for x in os.listdir(p.local_data) if x.endswith('.tar')]:
                tar_path = os.path.join(p.local_data, d)
                untar = subprocess.run(
                    ['tar', '-xf', tar_path, '-C', p.local_data, '--strip-components', '1'],
                    capture_output=True, encoding='utf-8',
                )
                if untar.returncode != 0:
                    raise IOError('Failed to extract {}: {}'.format(tar_path, untar.stderr.strip()))
                os.remove(tar_path)

    def uploadData(self, local_data, tarred=False, parallel=False, verify=False):
        # parallel=True tars (if requested) inline, then returns the rclone copy
        # command for the caller to run. verify=True runs `rclone check` after a
        # synchronous upload to confirm the transfer matches.
        if local_data is None:
            return

        p = self._localToCloud(local_data, tarred=tarred)

        if tarred:
            archive = subprocess.run(
                ['tar', '-cf', p.local_target, '-C', p.parent_dir, p.base],
                capture_output=True, encoding='utf-8',
            )
            if archive.returncode != 0 and '.DS_Store' not in archive.stderr:
                raise IOError('Failed to create archive {}: {}'.format(p.local_target, archive.stderr.strip()))

        if os.path.isdir(p.local_target):
            dest = p.cloud_target
        elif os.path.isfile(p.local_target):
            dest = p.cloud_parent
        else:
            raise FileNotFoundError(local_data + ' does not exist for upload')

        cmd = ['copy', p.local_target, dest]
        if parallel:
            return ['rclone'] + cmd

        result = self._run_rclone(cmd)
        if result.returncode != 0 and '.DS_Store' not in result.stderr:
            raise IOError(
                'Error uploading {} (rclone exit {}): {}'.format(
                    p.cloud_target, result.returncode, result.stderr.strip()
                )
            )

        if verify:
            checked = self._run_rclone(['check', p.local_target, dest])
            if checked.returncode != 0:
                raise IOError(
                    'Upload verification failed for {} (rclone exit {}): {}'.format(
                        p.cloud_target, checked.returncode, checked.stderr.strip()
                    )
                )

    # ------------------------------------------------------------------ #
    # Cloud queries / deletion                                            #
    # ------------------------------------------------------------------ #

    def checkFileExists(self, local_data, tarred=False):
        p = self._localToCloud(local_data, tarred=tarred)
        listing = self._run_rclone(['lsf', p.cloud_parent], allow_codes=_RCLONE_NOT_FOUND)
        if listing.returncode in _RCLONE_NOT_FOUND:
            return False
        if listing.returncode != 0:
            raise IOError(
                'rclone could not list {} (exit {}): {}'.format(
                    p.cloud_parent, listing.returncode, listing.stderr.strip()
                )
            )
        remotefiles = [x.rstrip('/') for x in listing.stdout.split('\n') if x]
        return p.relative_name in remotefiles

    def getCloudFiles(self, local_data, dirs_only=False, files_only=False):
        cloud_dir = self._cloudDir(local_data)
        listing = self._run_rclone(['lsf', cloud_dir], allow_codes=_RCLONE_NOT_FOUND)
        if listing.returncode in _RCLONE_NOT_FOUND:
            return []
        if listing.returncode != 0:
            raise IOError(
                'rclone could not list {} (exit {}): {}'.format(
                    cloud_dir, listing.returncode, listing.stderr.strip()
                )
            )
        entries = [x for x in listing.stdout.split('\n') if x]
        if dirs_only:
            entries = [x for x in entries if x.endswith('/')]
        elif files_only:
            entries = [x for x in entries if not x.endswith('/')]
        return [x.rstrip('/') for x in entries]

    def returnFileSize(self, local_data):
        p = self._localToCloud(local_data)
        result = self._run_rclone(['size', '--json', p.cloud_target], allow_codes=_RCLONE_NOT_FOUND)
        if result.returncode in _RCLONE_NOT_FOUND:
            raise FileNotFoundError('No cloud object to size: ' + p.cloud_target)
        if result.returncode != 0:
            raise IOError(
                'rclone size failed for {} (exit {}): {}'.format(
                    p.cloud_target, result.returncode, result.stderr.strip()
                )
            )
        return json.loads(result.stdout)['bytes']

    def deleteCloudData(self, local_data):
        if not self.checkFileExists(local_data):
            return
        cloud_dir = self._cloudDir(local_data)
        result = self._run_rclone(['purge', cloud_dir], allow_codes=_RCLONE_NOT_FOUND)
        if result.returncode != 0 and result.returncode not in _RCLONE_NOT_FOUND:
            raise IOError(
                'Error deleting {} (rclone exit {}): {}'.format(
                    cloud_dir, result.returncode, result.stderr.strip()
                )
            )
        if self.checkFileExists(local_data):
            raise IOError('Purge reported success but {} still exists in the cloud'.format(cloud_dir))