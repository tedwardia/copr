Summary: Remove old packages from rpm-md (yum) repository
Name: prunerepo
Version: 1.0
Release: 1%{?dist} 

# Source is created by:
# git clone https://git.fedorahosted.org/git/copr.git
# cd copr/prunerepo
# tito build --tgz
Source0: %{name}-%{version}.tar.gz

License: GPLv2+
BuildArch: noarch
BuildRequires: python3-devel
BuildRequires: rpm-python3
Requires: createrepo_c
Requires: dnf-plugins-core
Requires: rpm-python3
Requires: python3

%description
Removes obsoleted package versions from a yum repository. Both
rpms and srpms, that have a newer version available in that same
repository, are deleted from filesystem and rpm-md metadata are 
recreated afterwards. Support for specific repository structure
(e.g. COPR) is also available making it possible to additionally
remove build logs and whole build directories associated with a 
package. After deletion of obsoleted packages, the command
"createrepo_c --database --update" is called to recreate the
repository metadata.

%prep
%setup -q

%build
%py3_build

%install
%py3_install

%files
%license LICENSE

%{python3_sitelib}/*
%{_bindir}/prunerepo

%changelog
* Tue Jan 19 2016 clime <clime@redhat.com> 1.0-1
- Initial package version
