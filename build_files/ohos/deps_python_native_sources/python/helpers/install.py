#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Batch installation from explicit private source/object/prefix arguments."""
from pathlib import Path
import shutil
import sys
import sysconfig
sys.dont_write_bytecode=True
SOURCE,BUILD,PREFIX=[Path(p).resolve() for p in sys.argv[1:4]]
LIB=PREFIX/'lib/python3.13'
if not sysconfig.is_python_build() or sys.platform!='ohos' or sys.version_info[:3]!=(3,13,13):raise RuntimeError('Built native CPython3.13 required')
if PREFIX.exists():raise RuntimeError('Refusing existing runtime prefix')

def copy(source,destination,executable=False):
    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination);destination.chmod(0o755 if executable else 0o644)

def link(name,target):
    name.parent.mkdir(parents=True,exist_ok=True);name.symlink_to(target)

shutil.copytree(SOURCE/'Lib',LIB,copy_function=shutil.copyfile,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
(LIB/'site-packages').mkdir(exist_ok=True);copy(SOURCE/'LICENSE',LIB/'LICENSE.txt')
module_dir=BUILD/(BUILD/'pybuilddir.txt').read_text().strip()
for pattern in ('_sysconfigdata_*.py','_sysconfig_vars_*.json','build-details.json'):
    for file in module_dir.glob(pattern):copy(file,LIB/file.name)
copy(BUILD/'python',PREFIX/'bin/python3.13',True);link(PREFIX/'bin/python3','python3.13');link(PREFIX/'bin/python','python3.13')
soname=sysconfig.get_config_var('INSTSONAME');library=sysconfig.get_config_var('LDLIBRARY')
copy(BUILD/soname,PREFIX/'lib'/soname,True)
if soname!=library:link(PREFIX/'lib'/library,soname)
if (BUILD/'libpython3.so').exists():copy(BUILD/'libpython3.so',PREFIX/'lib/libpython3.so',True)
extensions=sorted(module_dir.glob('*.so'))
if len(extensions)<55:raise RuntimeError('Incomplete extension build')
for file in extensions:copy(file,LIB/'lib-dynload'/file.name,True)
include=PREFIX/'include/python3.13';shutil.copytree(SOURCE/'Include',include,copy_function=shutil.copyfile);copy(BUILD/'pyconfig.h',include/'pyconfig.h')
config=LIB/Path(sysconfig.get_config_var('LIBPL')).name
for source,name in [(BUILD/'Makefile','Makefile'),(BUILD/'Modules/config.c','config.c'),(SOURCE/'Modules/config.c.in','config.c.in'),(SOURCE/'Modules/Setup','Setup'),
 (BUILD/'Modules/Setup.local','Setup.local'),(BUILD/'Modules/Setup.bootstrap','Setup.bootstrap'),(BUILD/'Modules/Setup.stdlib','Setup.stdlib'),
 (SOURCE/'Modules/makesetup','makesetup'),(SOURCE/'install-sh','install-sh'),(BUILD/'python-config.py','python-config.py')]:
    if source.is_file():copy(source,config/name,name in ('makesetup','install-sh','python-config.py'))
static=sysconfig.get_config_var('LIBRARY')
if static and (BUILD/static).is_file():copy(BUILD/static,PREFIX/'lib'/static);link(config/static,'../../'+static)
for source,name,alias in [('python.pc','python-3.13.pc','python3.pc'),('python-embed.pc','python-3.13-embed.pc','python3-embed.pc')]:
    directory=PREFIX/'lib/pkgconfig';copy(BUILD/'Misc'/source,directory/name);link(directory/alias,name)
copy(SOURCE/'Misc/python.man',PREFIX/'share/man/man1/python3.13.1');link(PREFIX/'share/man/man1/python3.1','python3.13.1')
print('Installed signed3.13 runtime/extensions:',len(extensions),PREFIX)
