#!/usr/bin/python

import os
import json
import time
import shutil
import tempfile
import logging
from subprocess import PIPE, Popen, call

from requests import get, post

from .exceptions import PackageImportException, PackageDownloadException, PackageQueryException, GitAndTitoException, \
    SrpmBuilderException, GitException
from .srpm_import import do_git_srpm_import

from .helpers import FailTypeEnum

log = logging.getLogger(__name__)


class SourceType:
    SRPM_LINKS = 1
    SRPM_UPLOAD = 2
    GIT_AND_TITO = 3
    MOCK_SCM = 4


class Package:
    def __init__(self, srpm_path):
        self.name, self.version = self.pkg_name_evr(srpm_path)
        self.git_hash = None

    def to_dict(self):
        return {
            'name': self.name,
            'version': self.version,
            'git_hash': self.git_hash,
        }

    def pkg_name_evr(self, srpm_path):
        """
        Queries a package for its name and evr (epoch:version-release)
        """
        log.debug("Verifying packagage, getting  name and version.")
        cmd = ['rpm', '-qp', '--nosignature', '--qf', '%{NAME} %{EPOCH} %{VERSION} %{RELEASE}', srpm_path]
        try:
            proc = Popen(cmd, stdout=PIPE, stderr=PIPE)
            output, error = proc.communicate()
        except OSError as e:
            log.error(str(e))
            raise PackageQueryException(e)
        if proc.returncode != 0:
            log.error(error)
            raise PackageQueryException('Error querying srpm: %s' % error)

        try:
            name, epoch, version, release = output.split(" ")
        except ValueError as e:
            raise PackageQueryException(e)

        # Epoch is an integer or '(none)' if not set
        if epoch.isdigit():
            evr = "{}:{}-{}".format(epoch, version, release)
        else:
            evr = "{}-{}".format(version, release)

        return name, evr


class ImportTask(object):
    def __init__(self):

        self.task_id = None
        self.user = None
        self.project = None
        self.branch = None

        self.source_type = None
        self.source_json = None
        self.source_data = None

        self.packages = []

        # For SRPM_LINKS and SRPM_UPLOAD
        self.package_urls = None

        # For Git based providers (GIT_AND_TITO)
        self.git_url = None
        self.git_branch = None

        # For GIT_AND_TITO
        self.tito_git_dir = None
        self.tito_test = None

        # For MOCK_SCM
        self.mock_scm_type = None
        self.mock_scm_url = None
        self.mock_scm_branch = None
        self.mock_spec = None

    def get_reponame(self, package=None):
        if any(x is None for x in [self.user, self.project, package.name]):
            return None
        else:
            return "{}/{}/{}".format(self.user, self.project, package.name)

    @staticmethod
    def from_dict(dict_data, opts):
        task = ImportTask()

        task.task_id = dict_data["task_id"]
        task.user = dict_data["user"]
        task.project = dict_data["project"]

        task.branch = dict_data["branch"]
        task.source_type = dict_data["source_type"]
        task.source_json = dict_data["source_json"]
        task.source_data = json.loads(dict_data["source_json"])

        if task.source_type == SourceType.SRPM_LINKS:
            task.package_urls = json.loads(task.source_json)["urls"]

        elif task.source_type == SourceType.SRPM_UPLOAD:
            json_tmp = task.source_data["tmp"]
            json_pkg = task.source_data["pkg"]
            task.package_urls = ["{}/tmp/{}/{}".format(opts.frontend_base_url, json_tmp, json_pkg)]

        elif task.source_type == SourceType.GIT_AND_TITO:
            task.git_url = task.source_data["git_url"]
            task.git_branch = task.source_data["git_branch"]
            task.tito_git_dir = task.source_data["git_dir"]
            task.tito_test = task.source_data["tito_test"]

        elif task.source_type == SourceType.MOCK_SCM:
            task.mock_scm_type = task.source_data["scm_type"]
            task.mock_scm_url = task.source_data["scm_url"]
            task.mock_scm_branch = task.source_data["scm_branch"]
            task.mock_spec = task.source_data["spec"]

        else:
            raise PackageImportException("Got unknown source type: {}".format(task.source_type))

        return task

    def get_dict_for_frontend(self):
        return {
            "task_id": self.task_id,
            "packages": [package.to_dict() for package in self.packages],
        }


class SourceProvider(object):
    """
    Proxy to download sources and save them as SRPM
    """
    def __init__(self, task, target_dir):
        """
        :param ImportTask task:
        :param str target_dir:
        """
        self.task = task
        self.target_dir = target_dir

        if task.source_type == SourceType.SRPM_LINKS:
            self.provider = SrpmUrlsProvider

        elif task.source_type == SourceType.SRPM_UPLOAD:
            self.provider = SrpmUrlsProvider

        elif task.source_type == SourceType.GIT_AND_TITO:
            self.provider = GitAndTitoProvider

        elif task.source_type == SourceType.MOCK_SCM:
            self.provider = MockScmProvider

        else:
            raise PackageImportException("Got unknown source type: {}".format(task.source_type))

    def get_srpms(self):
        return self.provider(self.task, self.target_dir).get_srpms()


class BaseSourceProvider(object):
    def __init__(self, task, target_dir):
        self.task = task
        self.target_dir = target_dir


class SrpmBuilderProvider(BaseSourceProvider):
    def __init__(self, task, target_dir):
        super(SrpmBuilderProvider, self).__init__(task, target_dir)
        self.tmp = tempfile.mkdtemp()
        self.tmp_dest = tempfile.mkdtemp()

    def copy(self):
        # 4. copy srpm to the target destination
        log.debug("GIT_BUILDER: 4. get srpm paths")
        dest_files = os.listdir(self.tmp_dest)
        dest_srpms = filter(lambda f: f.endswith(".src.rpm"), dest_files)
        log.debug("dest_srpms: {}".format(dest_srpms))
        srpm_paths = []
        for dest_srpm in dest_srpms:
            source_path = os.path.join(self.target_dir, os.path.basename(dest_srpm))
            target_path = os.path.join(self.tmp_dest, os.path.basename(dest_srpm))
            shutil.copyfile(source_path, target_path)
            srpm_paths.append(target_path)
        return srpm_paths


class GitProvider(SrpmBuilderProvider):
    def __init__(self, task, target_dir):
        """
        :param ImportTask task:
        :param str target_dir:
        """
        # task.git_url
        # task.git_branch
        super(GitProvider, self).__init__(task, target_dir)
        self.git_dir = None

    def get_srpms(self):
        self.clone()
        self.checkout()
        self.build()
        srpm_paths = self.copy()
        self.clean()
        return srpm_paths

    def clone(self):
        # 1. clone the repo
        log.debug("GIT_BUILDER: 1. clone".format(self.task.source_type))
        cmd = ['git', 'clone', self.task.git_url]
        try:
            proc = Popen(cmd, stdout=PIPE, stderr=PIPE, cwd=self.tmp)
            output, error = proc.communicate()
        except OSError as e:
            log.error(str(e))
            raise GitException(FailTypeEnum("git_clone_failed"))
        if proc.returncode != 0:
            log.error(error)
            raise GitException(FailTypeEnum("git_clone_failed"))

        # 1b. get dir name
        log.debug("GIT_BUILDER: 1b. dir name...")
        cmd = ['ls']
        try:
            proc = Popen(cmd, stdout=PIPE, stderr=PIPE, cwd=self.tmp)
            output, error = proc.communicate()
        except OSError as e:
            log.error(str(e))
            raise GitException(FailTypeEnum("git_wrong_directory"))
        if proc.returncode != 0:
            log.error(error)
            raise GitException(FailTypeEnum("git_wrong_directory"))
        if output and len(output.split()) == 1:
            git_dir_name = output.split()[0]
        else:
            raise GitException(FailTypeEnum("git_wrong_directory"))
        log.debug("   {}".format(git_dir_name))

        self.git_dir = "{}/{}".format(self.tmp, git_dir_name)

    def checkout(self):
        # 2. checkout git branch
        log.debug("GIT_BUILDER: 2. checkout")
        if self.task.git_branch and self.task.git_branch != 'master':
            cmd = ['git', 'checkout', self.task.git_branch]
            try:
                proc = Popen(cmd, stdout=PIPE, stderr=PIPE, cwd=self.git_dir)
                output, error = proc.communicate()
            except OSError as e:
                log.error(str(e))
                raise GitException(FailTypeEnum("git_checkout_error"))
            if proc.returncode != 0:
                log.error(error)
                raise GitException(FailTypeEnum("git_checkout_error"))

    def build(self):
        raise NotImplemented

    def clean(self):
        # 5. delete temps
        log.debug("GIT_BUILDER: 5. delete tmp")
        shutil.rmtree(self.tmp)
        shutil.rmtree(self.tmp_dest)


class GitAndTitoProvider(GitProvider):
    """
    Used for GIT_AND_TITO
    """
    def build(self):
        # task.tito_test
        # task.tito_git_dir
        log.debug("GIT_BUILDER: 3. build via tito")
        cmd = ['tito', 'build', '-o', self.tmp_dest, '--srpm']
        if self.task.tito_test:
            cmd.append('--test')
        git_subdir = "{}/{}".format(self.git_dir, self.task.tito_git_dir)

        try:
            proc = Popen(cmd, stdout=PIPE, stderr=PIPE, cwd=git_subdir)
            output, error = proc.communicate()
        except OSError as e:
            log.error(str(e))
            raise GitAndTitoException(FailTypeEnum("srpm_build_error"))
        if proc.returncode != 0:
            log.error(error)
            raise GitAndTitoException(FailTypeEnum("srpm_build_error"))


class MockScmProvider(SrpmBuilderProvider):
    """
    Used for MOCK_SCM
    """
    def get_srpms(self):
        log.debug("Build via Mock")

        package_name = os.path.basename(self.task.mock_spec).replace(".spec", "")
        cmd = ["/usr/bin/mock", "-r", "epel-7-x86_64",
               "--scm-enable",
               "--scm-option", "method={}".format(self.task.mock_scm_type),
               "--scm-option", "package={}".format(package_name),
               "--scm-option", "branch={}".format(self.task.mock_scm_branch),
               "--scm-option", "write_tar=True",
               "--scm-option", "spec={0}".format(self.task.mock_spec),
               "--scm-option", self.scm_option_get(),
               "--buildsrpm", "--resultdir={}".format(self.tmp_dest)]

        try:
            proc = Popen(" ".join(cmd), shell=True, stdout=PIPE, stderr=PIPE)
            output, error = proc.communicate()
        except OSError as e:
            log.error(str(e))
            raise SrpmBuilderException(FailTypeEnum("srpm_build_error"))
        if proc.returncode != 0:
            log.error(error)
            raise SrpmBuilderException(FailTypeEnum("srpm_build_error"))

        return self.copy()

    def scm_option_get(self):
        return {
            "git": "git_get='git clone {}'",
            "svn": "git_get='git svn clone {}'"
        }[self.task.mock_scm_type].format(self.task.mock_scm_url)


class SrpmUrlsProvider(BaseSourceProvider):
    def get_srpms(self):
        """
        Used for SRPM_LINKS and SRPM_UPLOAD
        :param ImportTask task:
        :param str target_dir:
        :raises PackageDownloadException:
        :returns local fs paths to srpms
        """
        srpm_paths = []

        for pkg_url in self.task.package_urls:
            log.debug("download the package {}".format(pkg_url))

            try:
                r = get(pkg_url, stream=True, verify=False)
            except Exception:
                raise PackageDownloadException("Unexpected error during URL fetch: {}"
                                               .format(pkg_url))

            if 200 <= r.status_code < 400:
                try:
                    srpm_path = os.path.join(self.target_dir, os.path.basename(pkg_url))
                    with open(srpm_path, 'wb') as f:
                        for chunk in r.iter_content(1024):
                            f.write(chunk)
                    srpm_paths.append(srpm_path)
                except Exception as e:
                    raise PackageDownloadException("Unexpected error during URL retrieval: {}, {}"
                                                   .format(pkg_url, str(e)))
            else:
                raise PackageDownloadException("Failed to fetch: {} with HTTP status: {}"
                                               .format(pkg_url, r.status_code))
        return srpm_paths


class DistGitImporter(object):
    def __init__(self, opts):
        self.is_running = False
        self.opts = opts

        self.get_url = "{}/backend/importing/".format(self.opts.frontend_base_url)
        self.upload_url = "{}/backend/import-completed/".format(self.opts.frontend_base_url)
        self.auth = ("user", self.opts.frontend_auth)
        self.headers = {"content-type": "application/json"}

        self.tmp_root = None

    def try_to_obtain_new_task(self):
        log.debug("1. Try to get task data")
        try:
            # get the data
            r = get(self.get_url)
            # take the first task
            log.debug(r.text)
            builds_list = r.json()["builds"]
            if len(builds_list) == 0:
                log.debug("No new tasks to process")
                return
            return ImportTask.from_dict(builds_list[0], self.opts)
        except Exception:
            log.exception("Failed acquire new packages for import")
        return

    def git_import_srpm(self, task, filepath, package):
        """
        Imports a source rpm file into local dist git.
        Repository name is in the Copr Style: user/project/package
        filepath is a srpm file locally downloaded somewhere

        :type task: ImportTask
        """
        log.debug("importing srpm into the dist-git")

        tmp = tempfile.mkdtemp()
        try:
            return do_git_srpm_import(self.opts, filepath, task, tmp, package)
        finally:
            shutil.rmtree(tmp)

    @staticmethod
    def after_git_import(opts):
        log.debug("refreshing cgit listing")
        call(["/usr/share/dist-git/cgit_pkg_list.sh", opts.cgit_pkg_list_location])

    @staticmethod
    def before_git_import(task, package):
        reponame = task.get_reponame(package)
        log.debug("make sure repos exist: {}".format(reponame))
        call(["/usr/share/dist-git/git_package.sh", reponame])
        call(["/usr/share/dist-git/git_branch.sh", task.branch, reponame])

    def post_back(self, data_dict):
        """
        Could raise error related to networkd connection
        """
        log.debug("Sending back: \n{}".format(json.dumps(data_dict)))
        return post(self.upload_url, auth=self.auth, data=json.dumps(data_dict), headers=self.headers)

    def post_back_safe(self, data_dict):
        """
        Ignores any error
        """
        try:
            return self.post_back(data_dict)
        except Exception:
            log.exception("Failed to post back to frontend : {}".format(data_dict))

    def do_import(self, task):
        """
        :type task: ImportTask
        """
        log.info("2. Task: {}, importing the packages: {}"
                 .format(task.task_id, task.package_urls)) # TODO: package_urls for Git/Mock is None
        tmp_root = tempfile.mkdtemp()

        try:
            srpm_paths = SourceProvider(task, tmp_root).get_srpms()

            for srpm_path in srpm_paths:
                package = Package(srpm_path)

                self.before_git_import(task, package)
                package.git_hash = self.git_import_srpm(task, srpm_path, package)
                self.after_git_import(self.opts)

                task.packages.append(package)

            log.debug("sending a response - success")
            self.post_back(task.get_dict_for_frontend())

        except PackageImportException:
            log.exception("send a response - failure during import of: {}".format(task.package_urls))
            self.post_back_safe({"task_id": task.task_id, "error": "git_import_failed"})

        except PackageDownloadException:
            log.exception("send a response - failure during download of: {}".format(task.package_urls))
            self.post_back_safe({"task_id": task.task_id, "error": "srpm_download_failed"})

        except PackageQueryException:
            log.exception("send a response - failure during query of: {}".format(task.package_urls))
            self.post_back_safe({"task_id": task.task_id, "error": "srpm_query_failed"})

        except GitException as e:
            log.exception("send a response - failure during query of: {}".format(task.package_urls)) # TODO: package_urls for Git/Mock is None
            log.exception("... due to: {}".format(str(e)))
            self.post_back_safe({"task_id": task.task_id, "error": str(e)})

        except GitAndTitoException as e:
            log.exception("send a response - failure during 'Tito and Git' import of: {}".format(task.git_url))
            log.exception("   ... due to: {}".format(str(e)))
            self.post_back_safe({"task_id": task.task_id, "error": str(e)})

        except Exception:
            log.exception("Unexpected error during package import")
            self.post_back_safe({"task_id": task.task_id, "error": "unknown_error"})

        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    def run(self):
        log.info("DistGitImported initialized")

        self.is_running = True
        while self.is_running:
            mb_task = self.try_to_obtain_new_task()
            if mb_task is None:
                time.sleep(self.opts.sleep_time)
            else:
                self.do_import(mb_task)
