import requests
import os
import shutil
import subprocess
from six.moves.urllib.parse import urljoin
from backend.helpers import get_backend_opts
from backend.sign import sign_rpms_in_dir


opts = get_backend_opts()
here = os.path.dirname(os.path.realpath(__file__))


FRONTEND_URL = opts.frontend_base_url
BACKEND_DIR = os.path.dirname(here)
RESULTS_DIR = opts.destdir
RPMBUILD = os.path.join(os.path.expanduser("~"), "rpmbuild")

VERSION = 1
RELEASE = 1


class RepoRpmBuilder(object):

    """
    Purposed to create configuration RPM package for the repository.
    There have to be multiple configuration packages for the repo,
    so this builder needs to be instantiated with ``user`` and
    ``copr`` as expected, but also with ``chroot``.
    """

    RPM_NAME_FORMAT = "copr-repo-{}-{}-{}-{}-1-1.noarch.rpm"
    SPEC_NAME = "copr-repo-package.spec"

    def __init__(self, user, copr, chroot, log, topdir=RPMBUILD, resdir=RESULTS_DIR, opts=opts):
        self.user = user       # Name of the user
        self.copr = copr       # Name of the copr
        self.chroot = chroot   # MockChroot object
        self.topdir = topdir   # rpmbuild directory (default $HOME/rpmbuild)
        self.resdir = resdir   # Backend results directory accessible via http
        self.opts = opts
        self.log = log

    @property
    def os_release(self):
        return self.chroot.name.split("-")[0]

    @property
    def os_version(self):
        return self.chroot.name.split("-")[1]

    @property
    def name_release(self):
        return self.chroot.name[:self.chroot.name.rindex("-")]

    @property
    def rpm_name(self):
        version = self.os_version
        # All Fedora releases except for rawhide has same .repo file
        if self.os_release == "fedora" and self.os_version.isdigit():
            version = "all"
        return self.RPM_NAME_FORMAT.format(self.user, self.copr, self.os_release, version)

    @property
    def rpm_dir(self):
        return os.path.join(self.resdir, self.user, self.copr, "repo-packages")

    @property
    def repo_name(self):
        return "{}-{}-{}-{}.repo" \
            .format(self.user, self.copr, self.os_release, self.os_version)

    def has_repo_package(self):
        return os.path.isfile(os.path.join(self.rpm_dir, self.rpm_name))

    def get_repofile(self):
        api = "coprs/{}/{}/repo/{}/{}".format(self.user, self.copr, self.name_release, self.repo_name)
        url = urljoin(FRONTEND_URL, api)
        r = requests.get(url)
        if r.status_code != 200:
            raise RuntimeError("Can't get {}".format(url))
        return r.content

    def generate_repo_package(self):

        shutil.copyfile(os.path.join(BACKEND_DIR, "backend/static/", self.SPEC_NAME),
                        os.path.join(self.topdir, "SPECS", self.SPEC_NAME))

        with open(os.path.join(self.topdir, "SOURCES", self.repo_name), "w") as f:
            f.writelines(self.get_repofile())

        if not os.path.exists(self.rpm_dir):
            os.makedirs(self.rpm_dir)

        defines = [
            "-D",       "_topdir {}".format(self.topdir),
            "-D",       "_rpmdir {}".format(self.rpm_dir),
            "-D",  "_rpmfilename {}".format(self.rpm_name),
            "-D",      "pkg_name {}".format("copr-repo-{}-{}".format(self.user, self.copr)),
            "-D",   "pkg_version {}".format(VERSION),
            "-D",   "pkg_release {}".format(RELEASE),
            "-D",          "user {}".format(self.user),
            "-D",          "copr {}".format(self.copr),
            "-D",        "chroot {}".format("{}-{}".format(self.os_release, self.os_version)),
            "-D",      "repofile {}".format("_copr_{}-{}.repo".format(self.user, self.copr)),
        ]

        command = ["rpmbuild", "-ba"] + defines + [os.path.join(self.topdir, "SPECS", self.SPEC_NAME)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = process.communicate()

        if process.returncode != 0:
            raise RuntimeError("Failed rpmbuild for: {}\n{}".format(self.repo_name, err))

    def sign(self):
        sign_rpms_in_dir(self.user, self.copr, self.rpm_dir, self.opts, self.log)
