#!/usr/bin/python
# -*- coding: utf-8 -*-

# RUN
#     cd backend
#     python run/copr_generate_repo_packages.py -c ./conf/copr-be.local.conf


from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division
from __future__ import absolute_import

import os
import sys
import logging
from copr import create_client2_from_params

here = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.dirname(here))
sys.path.append("/usr/share/copr/")
from backend.repo_rpm import RepoRpmBuilder, FRONTEND_URL, RPMBUILD


LOG_FILE = "/var/log/copr/repo-packages.log"


log = logging.getLogger(__name__)
client = create_client2_from_params(FRONTEND_URL)


def all_coprs():
    return list(client.projects.get_list(limit=99999))


def unique_chroots(copr):
    d = {}
    for chroot in client.project_chroots.get_list(copr):
        name_release = chroot.name[:chroot.name.rindex("-")]
        d[name_release] = chroot
    return d.values()


def prepare_rpmbuild_directory():
    dirs = ["BUILD", "RPMS", "SOURCES", "SPECS", "SRPMS"]
    for d in dirs:
        d = os.path.join(RPMBUILD, d)
        if not os.path.exists(d):
            os.makedirs(d)


def main():
    prepare_rpmbuild_directory()

    for copr in all_coprs():
        for chroot in unique_chroots(copr):
            builder = RepoRpmBuilder(user=copr.owner, copr=copr.name, chroot=chroot, log=log)

            if builder.has_repo_package():
                log.info("Skipping {}".format(builder.repo_name))
                continue

            try:
                builder.generate_repo_package()
                log.info("Created RPM package for: {}".format(builder.repo_name))
                builder.sign()

            except RuntimeError as e:
                log.error(e.message)


if __name__ == "__main__":
    try:
        logging.getLogger("requests").setLevel(logging.WARNING)
        logging.basicConfig(
            filename=LOG_FILE,
            format='[%(asctime)s][%(levelname)6s]: %(message)s',
            level=logging.INFO)
        main()
    except KeyboardInterrupt:
        pass
