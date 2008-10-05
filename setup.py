#!/usr/bin/env python

"""
There are two relevant customizations to the standard distutils installation
process: (1) writing the nfoview.paths module and (2) handling translations.

(1) NFO Viewer finds non-Python files based on the paths written in module
    nfoview.paths. In nfoview/paths.py the paths default to the ones in the
    source directory. During the 'install_lib' command the file gets rewritten
    to build/nfoview/paths.py with the installation paths and that file will be
    installed. The paths are based on variable 'install_data' with the 'root'
    variable stripped if it is given. If doing distro-packaging, make sure this
    file gets correctly written.

(2) During installation, the .po files are compiled into .mo files and the
    desktop file is translated. This requires gettext and intltool, more
    specifically, executables 'msgfmt' and 'intltool-merge' in $PATH.
"""

import glob
import itertools
import os
import re
import shutil
import subprocess
import tarfile
import tempfile

from distutils import dir_util, log
from distutils.command.clean import clean
from distutils.command.install import install
from distutils.command.install_data import install_data
from distutils.command.install_lib import install_lib
from distutils.command.sdist import sdist
from distutils.core import setup

os.chdir(os.path.dirname(__file__) or ".")


def get_version_number():
    """Return version number from nfoview/__init__.py."""

    # Cannot import this, because importing __version__ from nfoview causes
    # a segfault if building or compiling outside X (with no $DISPLAY).
    text = open(os.path.join("nfoview", "__init__.py"), "r").read()
    return re.search(r"__version__ *= *['\"](.*?)['\"]", text).group(1)


class Clean(clean):

    """Command to remove files and directories created."""

    __glob_targets = ("build", "dist", "locale",
        "ChangeLog", "MANIFEST", "data/nfoview.desktop",)

    def run(self):
        """Remove files and directories listed in self.__targets."""

        clean.run(self)

        log.info("removing .pyc files under 'nfoview'")
        for (root, dirs, files) in os.walk("nfoview"):
            for name in (x for x in files if x.endswith(".pyc")):
                path = os.path.join(root, name)
                if not self.dry_run: os.remove(path)

        targets = [glob.glob(x) for x in self.__glob_targets]
        iterator = itertools.chain(*targets)
        for target in (x for x in iterator if os.path.isdir(x)):
            dir_util.remove_tree(target, dry_run=self.dry_run)
        iterator = itertools.chain(*targets)
        for target in (x for x in iterator if os.path.isfile(x)):
            log.info("removing '%s'" % target)
            if not self.dry_run: os.remove(target)


class Install(install):

    """Command to install everything."""

    def run(self):
        """Install everything and update the desktop file database."""

        install.run(self)

        get_command_obj = self.distribution.get_command_obj
        root = get_command_obj("install").root
        data_dir = get_command_obj("install_data").install_dir
        # Assume we're actually installing if --root was not given.
        if (root is not None) or (data_dir is None): return

        directory = os.path.join(data_dir, "share", "applications")
        log.info("updating desktop database in '%s'" % directory)
        try: subprocess.call(("update-desktop-database", directory))
        except OSError: log.info("...failed")


class InstallData(install_data):

    """Command to install data files."""

    def __get_desktop_file(self):
        """Return a tuple for the translated desktop file."""

        path = os.path.join("data", "nfoview.desktop")
        os.system("intltool-merge -d po %s.in %s" % (path, path))
        return ("share/applications", (path,))

    def __get_mo_file(self, po_file):
        """Return a tuple for the compiled .mo file."""

        locale = os.path.basename(po_file[:-3])
        mo_dir = os.path.join("locale", locale, "LC_MESSAGES")
        if not os.path.isdir(mo_dir):
            log.info("creating %s" % mo_dir)
            os.makedirs(mo_dir)
        mo_file = os.path.join(mo_dir, "nfoview.mo")
        dest_dir = os.path.join("share", mo_dir)
        log.info("compiling '%s'" % mo_file)
        os.system("msgfmt %s -o %s" % (po_file, mo_file))
        return (dest_dir, (mo_file,))

    def run(self):
        """Install data files after translating them."""

        for po_file in glob.glob("po/*.po"):
            self.data_files.append(self.__get_mo_file(po_file))
        self.data_files.append(self.__get_desktop_file())
        install_data.run(self)


class InstallLib(install_lib):

    """Command to install library files."""

    def install(self):
        """Install library files after writing changes."""

        # Allow --root to be used as a destination directory.
        root = self.distribution.get_command_obj("install").root
        parent = self.distribution.get_command_obj("install").install_data
        if root is not None:
            root = os.path.abspath(root)
            parent = os.path.abspath(parent)
            parent = parent.replace(root, "")
        data_dir = os.path.join(parent, "share", "nfoview")
        locale_dir = os.path.join(parent, "share", "locale")

        # Write changes to the nfoview.paths module.
        path = os.path.join(self.build_dir, "nfoview", "paths.py")
        text = open(path, "r").read()
        string = 'get_source_directory("data")'
        text = text.replace(string, repr(data_dir))
        assert text.count(repr(data_dir)) > 0
        string = 'get_source_directory("locale")'
        text = text.replace(string, repr(locale_dir))
        assert text.count(repr(locale_dir)) > 0
        open(path, "w").write(text)

        return install_lib.install(self)


class SDistGna(sdist):

    """Command to create a source distribution for gna.org."""

    __version = get_version_number()
    description = "create source distribution for gna.org"

    def finalize_options(self):
        """Set the distribution directory to 'dist/X.Y'."""

        # pylint: disable-msg=W0201
        sdist.finalize_options(self)
        branch = ".".join(self.__version.split(".")[:2])
        self.dist_dir = os.path.join(self.dist_dir, branch)

    def run(self):
        """Build tarballs and create additional files."""

        if os.path.isfile("ChangeLog"):
            os.remove("ChangeLog")
        os.system("tools/git2cl > ChangeLog")
        assert os.path.isfile("ChangeLog")
        sdist.run(self)
        basename = "nfoview-%s" % self.__version
        tarballs = os.listdir(self.dist_dir)
        os.chdir(self.dist_dir)

        # Compare tarball contents with working copy.
        temp_dir = tempfile.gettempdir()
        test_dir = os.path.join(temp_dir, basename)
        tobj = tarfile.open(tarballs[-1], "r")
        for member in tobj.getmembers():
            tobj.extract(member, temp_dir)
        log.info("comparing tarball (tmp) with working copy (../..)")
        os.system('diff -qr -x ".*" -x "*.pyc" ../.. %s' % test_dir)
        response = raw_input("Are all files in the tarball [Y/n]? ")
        if response.lower() == "n":
            raise SystemExit("Must edit MANIFEST.in.")
        dir_util.remove_tree(test_dir)

        # Create extra distribution files.
        log.info("calculating md5sums")
        os.system("md5sum * > %s.md5sum" % basename)
        log.info("creating '%s.changes'" % basename)
        source = os.path.join("..", "..", "ChangeLog")
        shutil.copyfile(source, "%s.changes" % basename)
        log.info("creating '%s.news'" % basename)
        source = os.path.join("..", "..", "NEWS")
        shutil.copyfile(source, "%s.news" % basename)
        for tarball in tarballs:
            log.info("signing '%s'" % tarball)
            os.system("gpg --detach %s" % tarball)


setup(
    name="nfoview",
    version=get_version_number(),
    requires=("gtk (>=2.8.0)",),
    platforms=("Platform Independent",),
    author="Osmo Salomaa",
    author_email="otsaloma@cc.hut.fi",
    url="http://home.gna.org/nfoview/",
    description="Viewer for NFO files",
    license="GPL",
    packages=("nfoview",),
    scripts=("bin/nfoview",),
    data_files=[
        ("share/man/man1", ("doc/nfoview.1",)),
        ("share/nfoview", (
            "data/preferences-dialog.glade",
            "data/ui.xml")),],
    cmdclass={
        "clean": Clean,
        "install": Install,
        "install_data": InstallData,
        "install_lib": InstallLib,
        "sdist_gna": SDistGna,},)
