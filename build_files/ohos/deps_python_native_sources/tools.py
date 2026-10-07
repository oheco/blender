# SPDX-License-Identifier: GPL-2.0-or-later
"""Whole original Perl build-tool package, separate from every target runtime library."""
import sys
sys.dont_write_bytecode=True
import json
from pathlib import Path
import shutil
import tempfile
from source_guard import HERE,repo_file,reconstruct,extract,inventory,sha
from io_utils import dump,owned


def verify_case_members(root,pin):
    contract=json.loads((HERE/'tools/perl-original-case-exception.json').read_text())
    for member in contract['original_members']:
        path=Path(root)/Path(member['path']).relative_to(pin['directory'])
        if path.is_symlink() or oct(path.stat().st_mode&0o777)!=member['mode']:raise ValueError('Original exceptional Perl member mode differs')
        if member['type']=='5':
            if not path.is_dir():raise ValueError('Original exceptional Perl directory type differs')
        elif not path.is_file() or path.stat().st_size!=member['size'] or sha(path)!=member['sha256']:raise ValueError('Original exceptional Perl regular member differs')
    return {'member_counts':contract['member_counts'],'original_member_modes_types_hashes':'PASS whole exact Pod/pod subtrees'}


def prepare_perl(args,root,runner):
    root=owned(root);pin=json.loads((HERE/'tools/perl-binary-input.lock.json').read_text())
    target=root/'tools'/pin['directory'];archive=root/'archives'/pin['filename']
    if target.exists() or archive.exists():raise ValueError('Fresh complete Perl tool subtree required')
    reconstruct(pin,archive)
    with tempfile.TemporaryDirectory(prefix='python-native-whole-perl-tool-',dir=args.tmp_dir) as td:
        source=Path(td)/pin['directory'];extract(archive,source,pin['directory'],case_pairs=pin['original_case_pairs'])
        records=inventory(source,case_pairs=pin['inventory_case_pairs'])
        if records!=json.loads(repo_file(pin['original_inventory']).read_text()):raise ValueError('Whole original Perl package differs')
        verify_case_members(source,pin)
        target.parent.mkdir(exist_ok=True);shutil.copytree(source,target,symlinks=True)
        # copytree preserves permissions and the original signed bytes; no resign/patch.
        if inventory(target,case_pairs=pin['inventory_case_pairs'])!=records:raise ValueError('Perl package copy lost data/case distinction')
    case_receipt=verify_case_members(target,pin)
    perl=target/'bin/perl5.44.0'
    if sha(perl)!='630ab3172ccd3cde05bc44aaf14997be83516c623dc43564281e5e01081eb152':raise ValueError('Original accepted generator binary differs')
    scope={'PERL5LIB':str(target/'lib/5.44.0')+':'+str(target/'lib/5.44.0/aarch64-linux')}
    version=runner.run([perl,'-e','print $^V'],'whole-original-perl-version',extra_env=scope)
    if version!='v5.44.0':raise ValueError('Actual whole-package native Perl generator identity differs')
    args.perl=perl;runner.env.update(scope)
    receipt={'schema':1,'kind':'complete original native build-tool binary prerequisite','original_archive_sha256':pin['sha256'],
             'original_binary_sha256':sha(perl),'inventory_sha256':sha(repo_file(pin['original_inventory'])),
             'actual_native_version':version,'source_registry':pin['actual_used_source_registry'],'adaptation_registry':pin['adaptation_source_registry'],
             'package_objects':len(records),'original_bytes_modes_preserved':True,'two_original_case_subtrees_preserved':True,'original_case_member_audit':case_receipt,
             'role':'OpenSSL Configure/code generation only; never copied into HAP/Python/NumPy payload',
             'fresh_native_Perl_tool_rebuild':'NOTRUN','OpenSSL_generator_execution':'subsequent real python-dep-openssl-0 command gate',
             'temporary_cleaned':True}
    dump(root/'receipts/perl-generator-origin.json',receipt)
    return receipt
