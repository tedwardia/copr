import os
import pipes
import socket
import time
import urlparse
import subprocess

from backend.vm_manage import PUBSUB_INTERRUPT_BUILDER

from .. import ans_utils
from ..ansible_shell import AnsibleShell
from ..helpers import get_redis_connection
from ..exceptions import BuilderError, AnsibleCallError, VmError
from ..constants import mockchain, rsync, DEF_BUILD_TIMEOUT


class Builder(object):

    def __init__(self, opts, hostname, job, logger):
        self.opts = opts
        self.hostname = hostname
        self.job = job
        self.timeout = self.job.timeout or DEF_BUILD_TIMEOUT
        self.repos =  []
        self.log = logger
        self.ansible_shell = AnsibleShell(self.hostname, self.log)

        self.buildroot_pkgs = self.job.buildroot_pkgs or ""
        self._remote_tempdir = self.opts.remote_tempdir
        self._remote_basedir = self.opts.remote_basedir

        self.remote_pkg_path = None
        self.remote_pkg_name = None


    @property
    def remote_build_dir(self):
        """
        Returns
        -------
        str
            path to the remote directory passed to the mockchain cmd
        """
        return self.tempdir + "/build/"


    @property
    def tempdir(self):
        """Creates remote directory if it hasn't been created yet (self._remote_tempdir != None)

        Returns
        -------
        string
            path to the created remote directory
        """
        if self._remote_tempdir:
            return self._remote_tempdir

        tempdir = "{0}/{1}-XXXXX".format(self._remote_basedir, "mockremote")

        self.ansible_call("/bin/mktemp -d {0}".format(tempdir))
        self.ansible_call("/bin/chmod 755 {0}".format(tempdir))

        self._remote_tempdir = tempdir
        return self._remote_tempdir


    def _get_remote_results_dir(self):
        if any(x is None for x in [self.remote_build_dir,
                                   self.remote_pkg_name,
                                   self.job.chroot]):
            return None
        # the pkg will build into a dir by mockchain named:
        # $tempdir/build/results/$chroot/$packagename
        return os.path.normpath(os.path.join(
            self.remote_build_dir, "results", self.job.chroot, self.remote_pkg_name))


    def modify_mock_chroot_config(self):
        """
        Modify mock config for current chroot.
        Packages in buildroot_pkgs are added to minimal buildroot.
        """
        if ("'{0} '".format(self.buildroot_pkgs) !=
                pipes.quote(str(self.buildroot_pkgs) + ' ')):

            # just different test if it contains only alphanumeric characters allowed in packages name
            raise BuilderError("buildroot_pkgs contain invalid characters (non-alphanumeric)")

        self.log.info("putting {0} into minimal buildroot of {1}"
                      .format(self.buildroot_pkgs, self.job.chroot))

        buildroot_cmd = "sed -i \"s/{pattern}/{replace_for}/\" {filename}".format(
            pattern = ".*chroot_setup_cmd.*",
            replace_for = "config_opts['chroot_setup_cmd'] = 'install @buildsys-build {0}'".format(self.buildroot_pkgs),
            filename = "/etc/mock/{0}.cfg".format(self.job.chroot)
        )
        self.ansible_call(buildroot_cmd, ["-u", "root"])

        if not self.job.enable_net:
            disable_networking_cmd = "sed -i \"s/{pattern}/{replace_for}/\" {filename}".format(
                pattern = ".*use_host_resolv.*",
                replace_for = "config_opts['use_host_resolv'] = False",
                filename = "/etc/mock/{0}.cfg".format(self.job.chroot)
            )
            self.ansible_call(disable_networking_cmd, ["-u", "root"])


    def collect_built_packages(self):
        """
        Returns
        -------
        str
            built packages (new-line separated)
        """
        self.log.info("Listing built binary packages...")
        result = self.ansible_call(
            "cd {0} && "
            "for f in `ls *.rpm |grep -v \"src.rpm$\"`; do"
            "   rpm -qp --qf \"%{{NAME}} %{{VERSION}}\n\" $f; "
            "done"
            .format(pipes.quote(self._get_remote_results_dir()))
        )
        built_packages = result.stdout
        self.log.info("Built packages:\n{}".format(built_packages))
        return built_packages


    def check_build_success(self):
        """
        Returns
        -------
        bool
            true if 'success' file was found on the remote builder
        """
        successfile_path = os.path.join(self._get_remote_results_dir(), "success")
        return self.ansible_call("/usr/bin/test -f {0}".format(successfile_path)).succeeded


    def download_job_pkg_to_builder(self):
        """
        Gets srpm to build from dist-git.
        """
        repo_url = "{}/{}.git".format(self.opts.dist_git_url, self.job.git_repo)

        self.log.info("Cloning Dist Git repo {}, branch {}, hash {}".format(
            self.job.git_repo, self.job.git_branch, self.job.git_hash))

        result = self.ansible_call(
            "rm -rf /tmp/build_package_repo && "
            "mkdir /tmp/build_package_repo && "
            "cd /tmp/build_package_repo && "
            "git clone {repo_url} && "
            "cd {pkg_name} && "
            "git checkout {git_hash} && "
            "fedpkg-copr --dist {branch} srpm"
            .format(repo_url=repo_url,
                    pkg_name=self.job.package_name,
                    git_hash=self.job.git_hash,
                    branch=self.job.git_branch)
        )

        # expected stdout:
        # ...
        # Wrote: /tmp/.../copr-ping/copr-ping-1-1.fc21.src.rpm

        try:
            self.remote_pkg_path = result.stdout.split("Wrote: ")[1].strip()
            self.remote_pkg_name = os.path.basename(self.remote_pkg_path).replace(".src.rpm", "")
        except Exception:
            raise BuilderError("Unable to parse package name and path from stdout: {}".format(result.stdout))

        self.log.info("Got srpm to build: {}".format(self.remote_pkg_path))


    def pre_process_repo_url(self, repo_url):
        """
        Returns
        -------
        str
           sanitized repo url with expanded variables
        """
        try:
            parsed_url = urlparse.urlparse(repo_url)
            if parsed_url.scheme == "copr":
                user = parsed_url.netloc
                prj = parsed_url.path.split("/")[1]
                repo_url = "/".join([self.opts.results_baseurl, user, prj, self.job.chroot])

            else:
                if "rawhide" in self.job.chroot:
                    repo_url = repo_url.replace("$releasever", "rawhide")
                # custom expand variables
                repo_url = repo_url.replace("$chroot", self.job.chroot)
                repo_url = repo_url.replace("$distname", self.job.chroot.split("-")[0])

            return pipes.quote(repo_url)
        except Exception as err:
            self.log.exception("Failed to pre-process repo url: {}".format(err))
            return None


    def gen_mockchain_command(self):
        """
        Returns
        -------
        str
            mockchain command to execute the build
        """
        buildcmd = "{} -r {} -l {} ".format(
            mockchain, pipes.quote(self.job.chroot),
            pipes.quote(self.remote_build_dir))

        for repo in self.job.chroot_repos_extended:
            repo = self.pre_process_repo_url(repo)
            if repo is not None:
                buildcmd += "-a {0} ".format(repo)

        for k, v in self.job.mockchain_macros.items():
            mock_opt = "--define={} {}".format(k, v)
            buildcmd += "-m {} ".format(pipes.quote(mock_opt))

        buildcmd += self.remote_pkg_path

        if self.timeout:
            buildcmd = "timeout {0} {1}".format(self.timeout, buildcmd)

        return buildcmd


    def setup_pubsub_handler(self):
        """
        NOTE: probably for build interrupting from a client, currently _unusued_
        """
        self.rc = get_redis_connection(self.opts)
        self.ps = self.rc.pubsub(ignore_subscribe_messages=True)
        channel_name = PUBSUB_INTERRUPT_BUILDER.format(self.hostname)
        self.ps.subscribe(channel_name)
        self.log.info("Subscribed to vm interruptions channel {}".format(channel_name))


    def check_pubsub(self):
        """
        NOTE: probably for build interrupting from a client, currently _unusued_
        """
        self.log.info("Checking pubsub channel")
        msg = self.ps.get_message()
        if msg is not None and msg.get("type") == "message":
            raise VmError("Build interrupted by msg: {}".format(msg["data"]))


    def build(self):
        """Do build.

        Returns
        -------
        str
            stdout of the mockchain command
        """
        # do some mock configuration changes
        self.modify_mock_chroot_config()

        # download the package to the builder
        self.download_job_pkg_to_builder()

        # construct the mockchain command
        buildcmd = self.gen_mockchain_command()

        # run the mockchain command
        ansible_build_result = self.ansible_call(buildcmd)

        # we know the command ended successfully but not
        # if the pkg was built successfully
        if not self.check_build_success():
            raise BuilderError("Success file is missing.")

        return ansible_build_result.stdout


    def download(self, target_dir):
        if self._get_remote_results_dir():
            self.log.info("Start retrieve results for: {0}".format(self.job))
            # download the pkg to destdir using rsync + ssh

            # make spaces work w/our rsync command below :(
            destdir = "'" + target_dir.replace("'", "'\\''") + "'"

            # build rsync command line from the above
            remote_src = "{}@{}:{}/*".format(self.opts.build_user,
                                             self.hostname,
                                             self._get_remote_results_dir())
            ssh_opts = "'ssh -o PasswordAuthentication=no -o StrictHostKeyChecking=no'"

            rsync_log_filepath = os.path.join(destdir, self.job.rsync_log_name)
            command = "{} -rlptDvH -e {} {} {}/ &> {}".format(
                rsync, ssh_opts, remote_src, destdir,
                rsync_log_filepath)

            try:
                cmd = subprocess.Popen(command, shell=True)
                cmd.wait()
                self.log.info("End retrieve results for: {0}".format(self.job))
            except Exception as error:
                err_msg = "Failed to download results from builder due to rsync error, see the rsync log file for details. Original error: {}".format(error)
                self.log.error(err_msg)
                raise BuilderError(err_msg)

            if cmd.returncode != 0:
                err_msg = "Failed to download results from builder due to rsync error, see the rsync log file for details."
                self.log.error(err_msg)
                raise BuilderError(err_msg, returncode=cmd.returncode)


    def check(self):
        """
        Checks if builder is ready.
        """
        try:
            # requires name resolve facility
            socket.gethostbyname(self.hostname)
        except IOError:
            raise BuilderError("{0} could not be resolved".format(self.hostname))

        try:
            # test for presence of mock and rsync on the builder
            result = self.ansible_call("/bin/rpm -q mock rsync")
        except AnsibleCallError:
            raise BuilderError("Build host `{0}` does not have mock or rsync installed"
                               .format(self.hostname), result.returncode, result.stdout, result.stderr)

        try:
            # test for path existence for mockchain and chroot config for this chroot
            result = self.ansible_call("/usr/bin/test -f {0}".format(mockchain))
        except AnsibleCallError:
            raise BuilderError("Build host `{}` missing mockchain binary `{}`"
                               .format(self.hostname, mockchain), result.returncode, result.stdout, result.stderr)

        try:
            # test for presence of the required chroot mock config
            result = self.ansible_call("/usr/bin/test -f /etc/mock/{}.cfg".format(self.job.chroot))
        except AnsibleCallError:
            raise BuilderError("Build host `{}` missing mock config for chroot `{}`"
                               .format(self.hostname, self.job.chroot), result.returncode, result.stdout, result.stderr)


    def ansible_call(self, *args, **kwargs):
        """Wrapper around self.ansible_shell.run to translate failed return codes to AnsibleCallErrors

        Returns
        -------
        str stdout of the executed command

        Raises
        ------
        AnsibleCallError
            child of BuilderError
        """
        result = self.ansible_shell.run(*args, **kwargs)
        if not result.succeeded:
            raise AnsibleCallError(result.stdout, result.returncode)
        return result
