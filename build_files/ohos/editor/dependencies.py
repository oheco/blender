# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only verifier for caller-sealed editor dependency prefixes.

Schema 1, kind ``editor-dependency-prefix-contract`` has exactly these keys::

    {"schema_version": 1, "kind": "editor-dependency-prefix-contract",
     "canonical_helper": {"path": <repository-relative>, "size": N, "sha256": H},
     "dependencies": {<each DEPENDENCIES name>: <dependency>}}

A dependency requires ``prefix`` (absolute caller path), ``status``
(``PENDING_NATIVE`` or ``PASS_NATIVE``), and ``recipe``. Recipe has ``entry``,
``inputs_lock`` and ``source_locks``; each is a repository-relative file reference
``{path, size, sha256}``. All current sealed_files are checked, including original
archive parts. Entry, sibling Python helpers and source locks must be in that
seal. Color169 and pure59 locks and the existing canonical volume helper are
immutable pins. No source builder is imported and no compiler/tool is invoked.

A ready dependency also supplies ``build_root`` (absolute producer state root),
``producer``, ``inventory``, ``metadata``, ``adapter``, ``native_receipt``,
``receipts`` and ``gates``. These file references have absolute paths. Receipts
maps producer-relative names to references; gates is a list of references.
``interfaces`` maps installed CMake target names to:
``{type: STATIC|SHARED|INTERFACE, config: relative .cmake path,
headers: [relative installed file], libraries: [relative .a/.so file],
requires: [target name or Threads::Threads/-lpthread/m/dl], definitions: [literal]}``.
Optional ``exports`` lists installed configuration-specific .cmake exports;
config and exports are checked together. Python::Python/Python::PythonStatic
may select Python's actual installed embedding .pc instead. Every installed
regular archive needs a declared interface and current SHA; the library recipes
also require their actual native archive audit. Core must expose exact static
Ceres::ceres and glog::glog closures individually, plus the actual ``gflags``
INTERFACE convenience target through ``gflags_static``/libgflags.a, and bind its
read-only TBB prerequisite to the independently accepted volume producer.
Literal -lpthread and empty exported DLL definitions (GFLAGS_DLL_DECLARE_FLAG=,
GFLAGS_DLL_DEFINE_FLAG=, GOOGLE_GLOG_DLL_DECL=) follow the installed exports;
no flags from a different Glog/Gflags version are synthesized.
No Ceres-to-glog/gflags edge is implied; actual CONFIG requires edges determine
the transitive closure. Pure resources use an empty interfaces map. Python
retains its own frozen Clang15 build profile.

The external inventory and metadata documents have exactly
``{schema_version:1, kind:editor-prefix-inventory|editor-prefix-metadata,
input_lock_sha256:H, producer_sha256:H, files:[...]}``.
Inventory rows are ``{path, kind:file, size, sha256}`` or
``{path, kind:symlink, target, target_size, target_sha256}``. Relative link
literals must resolve to regular files inside the prefix. Directories are
implicit; case collisions and special nodes fail. Metadata rows are file rows
and enumerate *all* .pc/.cmake/.la/.json/.cfg/.ini, METADATA/WHEEL/RECORD and
the exact Python sysconfig producer document below. Consumer metadata must
have no source/build/private cache route. Metadata alone may add ``scopes``::

    {"libexec/python3.13/tool-context.json":
       {"scope": "producer-tool-context", "producer_recipe_sha256": H,
        "native_receipt_sha256": H, "python_build_sha256": H,
        "final_signatures_sha256": H},
     "lib/python3.13/_sysconfigdata__ohos_aarch64-linux-ohos.py":
       {"scope": "producer-build-config", "producer_recipe_sha256": H,
        "native_receipt_sha256": H, "python_build_sha256": H,
        "final_signatures_sha256": H}}

Only those two exact paths in python_native accept producer scopes. Their
current files stay in the complete immutable inventories and must match the
actual runtime-manifest, source build and final-signature receipt chain and
current sealed python_finalize.py. Tool-context records are observed/pinned,
not asserted currently available or portable. Sysconfig's OHOS_BUILD_DEPS
must match that producer's deps root. Returned records.producer_metadata marks
portable=false and records the preserved producer routes. Unclassified known
documents or absent producer evidence become concrete gaps. No .pc/.cmake or
other JSON can acquire a producer exemption.

The external adapter has exactly ``{schema_version:1,
kind:editor-native-dependency-adapter, dependency, prefix, entry_sha256,
input_lock_sha256, source_locks, producer_sha256, inventory_sha256,
metadata_sha256, native_receipt_sha256, receipts:{name:sha256},
gates:[sha256]}``. It binds existing producer evidence; it does not replace it.
In particular Base's captured full output and Core's weak aggregate still need
actual producer ownership, source seals, complete current inventories, concrete
child receipts and signed runtime/log evidence. Nothing manufactures an upstream
full-native-acceptance file or upgrades a plan/source-only result to native PASS.

A gate document has exactly ``{schema_version:1,
kind:editor-native-command-evidence, dependency, role, input_lock_sha256,
producer_sha256, argv:[...], cwd:absolute, actual_exit:0, log:<reference>,
underlying_receipt:<reference or null>, artifacts:[<reference>],
audits:[<reference>], sentinel:<literal log substring>}``.
Roles required for libraries are full/configure/build/install/runtime/migration;
Python additionally needs embedding; pure needs terminal. Each gate binds an
actual exit-zero log. Runtime/embedding/terminal gates bind currently signed
AArch64 ELF bytes, an audit recording their hash/signature, and the executable
as argv[0]. Other gates use empty artifact/audit lists. Core's old Runner emits
only a log: null underlying_receipt is allowed *only* there, with actual exit=0
in that immutable log. All other gates link a producer command receipt with its
actual exit and log hash. Pure's real terminal-command.json instead binds
actual_exit/interpreter_sha256, while its JSON terminal.stdout.log must equal
the selected attempt's terminal result; that producer emits no log exit footer.
The independent pure terminal gate needs the wrapper's current/terminal.json
pointer and actual full/audit attempt result; data PASS is insufficient.
Toolkit/native SDK probes and official standalone SSE2NEON source materialization
belong to the calling editor stage. Optional top-level supplemental_inputs must
be empty here, preventing a second SSE2NEON provider.

Pending declarations may omit unavailable ready fields. Any supplied references
and current inventories are still validated. ``allow_pending=True`` returns
PREPARED_NOT_RUN plus concrete gaps; the default rejects every gap before a
caller can configure. The bundle's SHA must come independently from its caller.
This module never writes, adopts, rebases, signs, or copies a prefix.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct

DEPENDENCIES = ('base', 'core', 'geometry', 'volume', 'color', 'vulkan',
                'python_native', 'gltf', 'pure_resources')
_ROOT = 'build_files/ohos/'
_NAMESPACES = {name: _ROOT + 'deps_' + name + '_sources' for name in DEPENDENCIES
               if name not in ('pure_resources', 'gltf')}
_NAMESPACES['gltf'] = _ROOT + 'deps_gltf_sources'
_NAMESPACES['pure_resources'] = _ROOT + 'deps_python_resources_sources'
_RECIPE_DIRS = {name: namespace + ('' if name in ('python_native', 'gltf') else '/builder')
                for name, namespace in _NAMESPACES.items()}
_SOURCE_LOCKS = {name: [directory + '/sources.lock.json'] for name, directory in _RECIPE_DIRS.items()}
_SOURCE_LOCKS['core'] = [_NAMESPACES['core'] + '/provenance.json']
_SOURCE_LOCKS['python_native'].append(_NAMESPACES['python_native'] + '/tools.lock.json')
_SOURCE_LOCKS['gltf'].append(_NAMESPACES['gltf'] + '/patches.lock.json')
_SOURCE_LOCKS['pure_resources'] = [_NAMESPACES['pure_resources'] + '/sources.lock.json']
_IMMUTABLE_LOCKS = {
    'color': 'cba1f7fb17f39ca732ab2b4529fe7179b0aaf90fd108ad48a36dd1ff11980d21',
    'pure_resources': '29224b69137f2b7a608fe9be838f8ad28c7a129f2e2a66fa54edd2ca7ff54a46',
}
_HELPER = 'build_files/cmake/platform/platform_ohos_volume.cmake'
_HELPER_SHA = '41499704255cfaddbd23300ca8d312b2b10a9ee2cfc15146ac6dcf1dd8b4351c'
_MARKERS = {'base': 'owner.json', 'core': '.core-builder-owned.json',
            'geometry': '.geometry-builder-owned.json', 'volume': '.volume-builder-owned.json',
            'color': '.color-builder-owned.json', 'vulkan': '.vulkan-builder-owned.json',
            'gltf': '.gltf-builder-owned.json', 'python_native': '.python-native-builder-owned.json',
            'pure_resources': '.pure-builder-owned.json'}
_FULL = {
    'base': ('native_full', 'PASS actual new full, including migration'),
    'core': ('result', 'PASS new-root complete core native dependency pipeline and moved-prefix consumers'),
    'geometry': ('result', 'PASS new complete source-built geometry and moved Unicode/space CMAKE/PC native consumers'),
    'volume': ('result', 'PASS actual independent complete selected-source native pipeline and migrated CMake/PC consumers'),
    'color': ('result', 'PASS actual independent complete selected-source native pipeline and migrated CMake/PC consumers'),
    'vulkan': ('result', 'PASS complete new source-built offline native Vulkan libraries and moved CMAKE/PC consumers'),
    'gltf': ('result', 'PASS actual independent source-built native full and moved pipeline'),
}
_VOLUME_TARGETS = {
    'TBB::tbb': ('lib/libtbb.a', ['Threads::Threads', 'dl', 'm'], ['__TBB_NO_IMPLICIT_LINKAGE']),
    'TBB::tbbmalloc': ('lib/libtbbmalloc.a', ['Threads::Threads', 'dl', 'm'], []),
    'Imath::Imath': ('lib/libImath-3_2.a', ['Threads::Threads', 'm'], []),
    'ZLIB::ZLIB': ('lib/libz.a', ['Threads::Threads', 'm'], []),
}
_READY = {'build_root', 'producer', 'inventory', 'metadata', 'adapter', 'native_receipt',
          'receipts', 'gates', 'interfaces'}
_METADATA_SUFFIXES = {'.pc', '.cmake', '.la', '.json', '.cfg', '.ini'}
_PYTHON_PRODUCER_METADATA = {
    'libexec/python3.13/tool-context.json': 'producer-tool-context',
    'lib/python3.13/_sysconfigdata__ohos_aarch64-linux-ohos.py': 'producer-build-config',
}


def sha(path: Path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _require(value, message):
    if not value:
        raise ValueError(message)


def _keys(value, required, optional=()):
    _require(isinstance(value, dict), 'Expected a JSON object')
    if 'schema_version' in value:
        _require(type(value['schema_version']) is int and value['schema_version'] == 1, 'Schema must be integer 1')
    _require(set(required) <= set(value) <= set(required) | set(optional),
             'Missing or unknown fields: ' + repr(sorted(set(value) ^ set(required))))


def _hash(value):
    _require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value), 'Invalid SHA256')
    return value


def canonical(value: str) -> str:
    _require(isinstance(value, str) and value and '\\' not in value and ':' not in value and
             not any(ord(c) < 32 or ord(c) == 127 for c in value), 'Unsafe relative path')
    path = PurePosixPath(value)
    _require(not path.is_absolute() and all(p not in ('', '.', '..') for p in value.split('/'))
             and path.as_posix() == value, 'Noncanonical relative path: ' + value)
    return value


def _absolute(value) -> Path:
    _require(isinstance(value, str) and value.startswith('/') and not value.startswith('//') and
             '\\' not in value and not any(ord(c) < 32 or ord(c) == 127 for c in value),
             'Expected a safe absolute caller path')
    path = Path(value)
    _require(path.as_posix() == value and all(p not in ('.', '..') for p in value.split('/')[1:]),
             'Noncanonical absolute path')
    _require(not any(p.is_symlink() for p in (path, *path.parents)), 'Symlink in caller path: ' + value)
    return path


def _json(path):
    def pairs(rows):
        result = {}
        for key, value in rows:
            _require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Nonfinite JSON number: ' + value)
    with Path(path).open(encoding='utf-8') as stream:
        return json.load(stream, object_pairs_hook=pairs, parse_constant=constant)


def _file(reference, source=None):
    _keys(reference, {'path', 'size', 'sha256'})
    _require(type(reference['size']) is int and reference['size'] >= 0, 'Invalid file size')
    _hash(reference['sha256'])
    path = _absolute(reference['path']) if source is None else _absolute(
        (source / canonical(reference['path'])).as_posix())
    _require(path.is_file() and stat.S_ISREG(path.lstat().st_mode), 'Missing regular file: ' + str(path))
    _require(path.stat().st_size == reference['size'] and sha(path) == reference['sha256'],
             'Current file SHA/size differs: ' + str(path))
    return path


def _owned(reference, root):
    path = _file(reference)
    _require(path.is_relative_to(root), 'Receipt outside declared producer root: ' + str(path))
    return path


def _recipe(source, name, recipe):
    _keys(recipe, {'entry', 'inputs_lock', 'source_locks'})
    directory = _RECIPE_DIRS[name]
    _require(recipe['entry']['path'] == directory + '/builder.py' and
             recipe['inputs_lock']['path'] == directory + '/inputs.lock.json', 'Wrong source recipe paths')
    entry = _file(recipe['entry'], source)
    lock_path = _file(recipe['inputs_lock'], source)
    seal = recipe['inputs_lock']['sha256']
    if name in _IMMUTABLE_LOCKS:
        _require(seal == _IMMUTABLE_LOCKS[name], 'Immutable Color/pure input seal changed')
    lock = _json(lock_path)
    version = lock.get('schema_version', lock.get('schema'))
    _require(type(version) is int and version == 1 and
             isinstance(lock.get('sealed_files'), list) and lock['sealed_files'], 'Unsealed recipe inputs')
    indexed = {}
    for row in lock['sealed_files']:
        _file(row, source)
        _require(row['path'] not in indexed, 'Duplicate source input')
        indexed[row['path']] = row
    _require(indexed.get(recipe['entry']['path']) == recipe['entry'], 'Recipe entry is not source-sealed')
    for helper in entry.parent.glob('*.py'):
        _require(helper.relative_to(source).as_posix() in indexed, 'Unsealed sibling Python helper')
    _require(isinstance(recipe['source_locks'], list), 'Source locks must be a list')
    locks = {row['path']: row for row in recipe['source_locks']}
    _require(len(locks) == len(recipe['source_locks']) and set(locks) == set(_SOURCE_LOCKS[name]),
             'Missing or substituted original source/provenance locks')
    for row in locks.values():
        _file(row, source)
        _require(indexed.get(row['path']) == row, 'Source lock not tied to recipe seal')
    return seal


def _inventory(prefix):
    _require(prefix.is_dir(), 'Installed prefix missing: ' + str(prefix))
    rows, spellings = [], {}
    def walk(directory):
        for item in sorted(os.scandir(directory), key=lambda row: row.name):
            path = Path(item.path)
            rel = canonical(path.relative_to(prefix).as_posix())
            old = spellings.setdefault(rel.casefold(), rel)
            _require(old == rel, 'Case-colliding installed path')
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                walk(path)
            elif stat.S_ISREG(mode):
                rows.append({'path': rel, 'kind': 'file', 'size': path.stat().st_size, 'sha256': sha(path)})
            elif stat.S_ISLNK(mode):
                literal = os.readlink(path)
                _require(literal and not PurePosixPath(literal).is_absolute() and '\\' not in literal and
                         not any(ord(c) < 32 or ord(c) == 127 for c in literal), 'Unsafe installed link literal')
                target = path.resolve(strict=True)
                _require(target.is_relative_to(prefix) and target.is_file(), 'Installed link escapes/dangles')
                rows.append({'path': rel, 'kind': 'symlink', 'target': literal,
                             'target_size': target.stat().st_size, 'target_sha256': sha(target)})
            else:
                raise ValueError('Special installed file: ' + rel)
    walk(prefix)
    return sorted(rows, key=lambda row: row['path'])


def _document(reference, root, kind, seal, producer_sha):
    path = _owned(reference, root)
    value = _json(path)
    _keys(value, {'schema_version', 'kind', 'input_lock_sha256', 'producer_sha256', 'files'},
          {'scopes'} if kind == 'editor-prefix-metadata' else set())
    _require(value['schema_version'] == 1 and value['kind'] == kind and
             value['input_lock_sha256'] == seal and value['producer_sha256'] == producer_sha,
             'Inventory/metadata producer lineage differs')
    _require(isinstance(value['files'], list), 'Expected inventory rows')
    for row in value['files']:
        _require(isinstance(row, dict), 'Inventory row must be an object')
        canonical(row['path'])
        if row.get('kind') == 'file':
            _keys(row, {'path', 'kind', 'size', 'sha256'})
            _require(type(row['size']) is int and row['size'] >= 0, 'Invalid inventory file size')
            _hash(row['sha256'])
        else:
            _keys(row, {'path', 'kind', 'target', 'target_size', 'target_sha256'})
            _require(row['kind'] == 'symlink' and isinstance(row['target'], str) and
                     type(row['target_size']) is int and row['target_size'] >= 0, 'Invalid inventory link row')
            _hash(row['target_sha256'])
    return value if kind == 'editor-prefix-metadata' else value['files']


def _metadata_path(path):
    return (Path(path).suffix.lower() in _METADATA_SUFFIXES or
            Path(path).name in ('METADATA', 'WHEEL', 'RECORD') or path in _PYTHON_PRODUCER_METADATA)


def _python_producer_metadata(row, scope, source, root, declaration):
    """Observe two original producer documents; never call their routes portable."""
    _keys(scope, {'scope', 'producer_recipe_sha256', 'native_receipt_sha256',
                  'python_build_sha256', 'final_signatures_sha256'})
    _require(scope['scope'] == _PYTHON_PRODUCER_METADATA[row['path']], 'Wrong Python producer metadata scope')
    finalizer = source / _NAMESPACES['python_native'] / 'python_finalize.py'
    _require(scope['producer_recipe_sha256'] == sha(finalizer), 'Producer metadata finalizer source differs')
    references = declaration['receipts']
    native_ref = declaration['native_receipt']
    build_ref = references['receipts/python-build.json']
    signatures_ref = references['receipts/python-final-signatures.json']
    _require(scope['native_receipt_sha256'] == native_ref['sha256'] and
             scope['python_build_sha256'] == build_ref['sha256'] and
             scope['final_signatures_sha256'] == signatures_ref['sha256'], 'Producer metadata receipt links differ')
    native = _json(_owned(native_ref, root))
    build = _json(_owned(build_ref, root))
    signatures = _json(_owned(signatures_ref, root))
    seal = declaration['recipe']['inputs_lock']['sha256']
    source_lock = next(item['sha256'] for item in declaration['recipe']['source_locks']
                       if item['path'].endswith('/sources.lock.json'))
    _require(Path(native_ref['path']) == root / 'runtime-manifest.json' and
             Path(build_ref['path']) == root / 'receipts/python-build.json' and
             Path(signatures_ref['path']) == root / 'receipts/python-final-signatures.json' and
             native.get('kind') == 'python-native-runtime-source-replay' and native.get('inputs_lock_sha256') == seal and
             native.get('source_lock_sha256') == source_lock and
             native.get('actual_fresh_native_and_selected_pure8') == 'PASS only after actual commands above' and
             native.get('counts') == {'runtime_ELF': 93, 'HAP_DSO': 92, 'native_map': 90, 'pure_distributions': 8},
             'Producer metadata lacks actual new Python native pipeline lineage')
    _require(any(item.get('path') == 'receipts/python-build.json' and item.get('sha256') == build_ref['sha256']
                 for item in native.get('fresh_receipts', [])) and
             build.get('source_native_build') == 'PASS actual commands only' and
             build.get('source_lock_sha256') == source_lock and build.get('runtime') == 'runtime' and
             build.get('genuine_SONAME') == 'libpython3.13.so', 'Producer metadata lacks actual source build')
    _require(type(signatures.get('count')) is int and signatures['count'] == 74 and
             isinstance(signatures.get('files'), list) and len(signatures['files']) == 74 and
             len(set(signatures['files'])) == 74 and
             signatures.get('source_selected_SONAME') == 'libpython3.13.so' and
             signatures.get('metadata_only_normalization_before_final_sign') is True and
             signatures.get('postsign_binary_rename') is False, 'Python finalization/signature ordering not proven')
    _require(any(item.get('path') == row['path'] and item.get('sha256') == row['sha256'] and
                 type(item.get('size')) is int and item['size'] == row['size']
                 for item in native.get('runtime_inventory', [])), 'Producer metadata bytes lack native manifest binding')
    path = Path(declaration['prefix']) / row['path']
    result = {'path': row['path'], 'scope': scope['scope'], 'sha256': row['sha256'],
              'producer_recipe_sha256': scope['producer_recipe_sha256'],
              'native_receipt_sha256': native_ref['sha256'], 'portable': False}
    if scope['scope'] == 'producer-tool-context':
        context = _json(path)
        _keys(context, {'tools', 'sdk_root', 'resource_dir', 'tmp_dir'})
        _keys(context['tools'], {'cc', 'cxx', 'lld', 'ar', 'ranlib', 'signer', 'readelf'})
        routes = [context[key] for key in ('sdk_root', 'resource_dir', 'tmp_dir')]
        for tool in context['tools'].values():
            _keys(tool, {'path', 'sha256'})
            _hash(tool['sha256'])
            routes.append(tool['path'])
        for route in routes:
            _require(isinstance(route, str) and Path(route).is_absolute() and
                     Path(route).as_posix() == route and '..' not in Path(route).parts and
                     not any(ord(c) < 32 or ord(c) == 127 for c in route), 'Invalid preserved producer tool route')
        # Record producer SDK/tool pins; their current availability/portability is not claimed.
        result['producer_routes'] = context
    else:
        build_deps = str(root / 'deps')
        _require(path.read_text().endswith('\nbuild_time_vars["OHOS_BUILD_DEPS"] = ' + repr(build_deps) + '\n'),
                 'Sysconfig producer dependency route differs from sealed finalizer behavior')
        result['producer_routes'] = {'OHOS_BUILD_DEPS': build_deps}
    return result


def _metadata(prefix, rows, supplied, source, build_root, name, declaration, gaps):
    expected = [row for row in rows if _metadata_path(row['path'])]
    _require(all(row['kind'] == 'file' for row in expected), 'Metadata symlink refused')
    _require(supplied['files'] == expected, 'Installed metadata inventory is incomplete or changed')
    scopes = supplied.get('scopes', {})
    _require(isinstance(scopes, dict) and (not scopes or name == 'python_native') and
             set(scopes) <= set(_PYTHON_PRODUCER_METADATA) and
             set(scopes) <= {row['path'] for row in expected}, 'Unknown/absent producer metadata scope')
    for path, scope in scopes.items():
        _keys(scope, {'scope', 'producer_recipe_sha256', 'native_receipt_sha256',
                      'python_build_sha256', 'final_signatures_sha256'})
        _require(scope['scope'] == _PYTHON_PRODUCER_METADATA[path], 'Wrong producer metadata classification')
        for key in ('producer_recipe_sha256', 'native_receipt_sha256', 'python_build_sha256', 'final_signatures_sha256'):
            _hash(scope[key])
        _require(scope['producer_recipe_sha256'] == sha(source / _NAMESPACES['python_native'] / 'python_finalize.py'),
                 'Metadata scope is not tied to the current sealed source finalizer')
    observed = []
    for row in expected:
        if name == 'python_native' and row['path'] in _PYTHON_PRODUCER_METADATA:
            if row['path'] not in scopes:
                gaps.append({'dependency': name, 'reason': 'Known Python producer metadata requires explicit scope: ' + row['path']})
                continue
            needed = {'receipts/python-build.json', 'receipts/python-final-signatures.json'}
            if 'native_receipt' not in declaration or not needed <= set(declaration.get('receipts', {})):
                gaps.append({'dependency': name, 'reason': 'Producer metadata scope lacks actual native build/sign receipts: ' + row['path']})
                continue
            observed.append(_python_producer_metadata(row, scopes[row['path']], source, build_root, declaration))
            continue
        text = (prefix / row['path']).read_text(encoding='utf-8')
        _require(not any(str(p) in text for p in (source, build_root, prefix)) and
                 not re.search(r'/(?:data/storage|storage/Users|Users/|home/|tmp/|var/tmp/)', text),
                 'Private source/build route in consumer metadata: ' + row['path'])
    return observed


def _interfaces(prefix, rows, value, name):
    _require(isinstance(value, dict), 'Target map must be an object')
    index = {row['path']: row for row in rows}
    covered = set()
    for target, item in value.items():
        _require(isinstance(target, str) and re.fullmatch(r'[A-Za-z0-9_.:+-]+', target), 'Unsafe target name')
        _keys(item, {'type', 'config', 'headers', 'libraries', 'requires', 'definitions'}, {'exports'})
        _require(item['type'] in ('STATIC', 'SHARED', 'INTERFACE'), 'Unknown imported target type')
        config = canonical(item['config'])
        python_pc = name == 'python_native' and config.endswith('.pc') and target in ('Python::Python', 'Python::PythonStatic')
        _require(config in index and index[config]['kind'] == 'file' and
                 (config.endswith('.cmake') or python_pc), 'Target lacks installed CONFIG/embedding metadata')
        config_text = (prefix / config).read_text()
        _require(isinstance(item.get('exports', []), list), 'Installed exports must be a list')
        for export in item.get('exports', []):
            canonical(export)
            _require(export in index and index[export]['kind'] == 'file' and export.endswith('.cmake'),
                     'Missing installed target/configuration export')
            config_text += '\n' + (prefix / export).read_text()
        _require('Name: Python' in config_text if python_pc else target in config_text,
                 'Declared target absent from its installed CONFIG/export')
        for key in ('headers', 'libraries', 'requires', 'definitions'):
            _require(isinstance(item[key], list) and len(item[key]) == len(set(item[key])) and
                     all(isinstance(v, str) and v for v in item[key]), 'Invalid target interface list')
        _require(item['headers'], 'Target needs concrete installed header evidence')
        for header in item['headers']:
            canonical(header)
            _require(header.startswith('include/') and header in index, 'Public target header absent')
        for library in item['libraries']:
            canonical(library)
            _require(library in index and index[library]['kind'] == 'file', 'Actual target library absent')
            file = prefix / library
            if item['type'] == 'STATIC':
                with file.open('rb') as stream:
                    _require(library.endswith('.a') and stream.read(8) == b'!<arch>\n', 'Fake/thin static archive')
            elif item['type'] == 'SHARED':
                _signed_elf(file)
            else:
                raise ValueError('INTERFACE target cannot own native libraries')
            binding = '-lpython3.13' if python_pc and file.name in ('libpython3.13.so', 'libpython3.13.a') else file.name
            _require(binding in config_text, 'Target archive/DLL not bound by its installed export')
            covered.add(library)
        _require(item['type'] == 'INTERFACE' or item['libraries'], 'Bare target lacks library binding')
        for dep in item['requires']:
            _require(dep in value or dep in ('Threads::Threads', '-lpthread', 'm', 'dl') or dep in _VOLUME_TARGETS,
                     'Undeclared static target closure: ' + dep)
            _require(dep in config_text, 'Private link interface absent from installed metadata: ' + dep)
        for define in item['definitions']:
            _require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:=[A-Za-z0-9_]*)?', define) and define in config_text,
                     'Unbound compile definition')
    archives = {row['path'] for row in rows if row['kind'] == 'file' and row['path'].endswith('.a')}
    _require(archives <= covered, 'Static archives without closed CONFIG interfaces: ' + repr(sorted(archives - covered)))
    if name == 'pure_resources':
        _require(not value and not archives, 'Pure resource prefix contains native interfaces')
    if name == 'core':
        for target, library in (('Ceres::ceres', 'lib/libceres.a'),
                                ('glog::glog', 'lib/libglog.a'), ('gflags', 'lib/libgflags.a')):
            _require(target in value, 'Core canonical Ceres/glog/gflags target missing: ' + target)
            pending, visited, closure = [target], set(), set()
            while pending:
                current = pending.pop()
                if current in visited or current not in value:
                    continue
                visited.add(current)
                closure.update(value[current]['libraries'])
                pending.extend(value[current]['requires'])
            _require(library in closure, 'Core imported target lacks exact static dependency closure: ' + target)
            if target == 'gflags':
                _require(value[target]['type'] == 'INTERFACE' and 'gflags_static' in visited and
                         value['gflags_static']['type'] == 'STATIC' and
                         library in value['gflags_static']['libraries'],
                         'Core gflags convenience interface must traverse its actual gflags_static archive target')
    if name == 'volume':
        for target, (library, requires, definitions) in _VOLUME_TARGETS.items():
            _require(target in value and value[target]['type'] == 'STATIC' and
                     value[target]['libraries'] == [library] and value[target]['requires'] == requires and
                     value[target]['definitions'] == definitions, 'Canonical volume interface differs: ' + target)
    return archives


def _signed_elf(path):
    # Inspect the current ELF section table; receipt booleans alone are insufficient.
    with Path(path).open('rb') as stream:
        header = stream.read(64)
        _require(len(header) == 64 and header[:6] == b'\x7fELF\x02\x01' and
                 struct.unpack_from('<H', header, 18)[0] == 183, 'Current runtime artifact is not AArch64 ELF64')
        offset = struct.unpack_from('<Q', header, 40)[0]
        size, count, strings = struct.unpack_from('<HHH', header, 58)
        _require(size == 64 and 0 < count < 65535 and 0 < strings < count, 'Invalid ELF section table')
        stream.seek(offset)
        sections = stream.read(size * count)
        _require(len(sections) == size * count, 'Truncated ELF section table')
        string_offset, string_size = struct.unpack_from('<QQ', sections, strings * size + 24)
        _require(0 < string_size <= 4 * 1024 * 1024, 'Invalid ELF section name table')
        stream.seek(string_offset)
        names = stream.read(string_size)
        _require(len(names) == string_size, 'Truncated ELF section names')
        found = False
        for index in range(count):
            name_offset = struct.unpack_from('<I', sections, index * size)[0]
            end = names.find(b'\x00', name_offset)
            if 0 <= name_offset < len(names) and end >= 0 and names[name_offset:end] == b'.codesign':
                section_size = struct.unpack_from('<Q', sections, index * size + 32)[0]
                found = section_size > 0
        _require(found, 'Current native artifact lacks a nonempty .codesign section')


def _audit_hash(value, digest):
    if isinstance(value, dict):
        if value.get('sha256') == digest and (value.get('codesign') is True or
                value.get('codesign_present') is True or value.get('signed') is True or
                value.get('signature_section') == '.codesign'):
            return True
        return any(_audit_hash(child, digest) for child in value.values())
    if isinstance(value, list):
        return any(_audit_hash(child, digest) for child in value)
    return False


def _gate(reference, name, root, seal, producer_sha, terminal=None):
    gate = _json(_owned(reference, root))
    _keys(gate, {'schema_version', 'kind', 'dependency', 'role', 'input_lock_sha256', 'producer_sha256',
                 'argv', 'cwd', 'actual_exit', 'log', 'underlying_receipt', 'artifacts', 'audits', 'sentinel'})
    _require(gate['schema_version'] == 1 and gate['kind'] == 'editor-native-command-evidence' and
             gate['dependency'] == name and gate['input_lock_sha256'] == seal and
             gate['producer_sha256'] == producer_sha and type(gate['actual_exit']) is int and
             gate['actual_exit'] == 0, 'Native command evidence incomplete or belongs to another producer')
    _require(gate['role'] in ('full', 'configure', 'build', 'install', 'runtime', 'migration', 'embedding', 'terminal'),
             'Unknown native gate role')
    _require(isinstance(gate['argv'], list) and gate['argv'] and
             all(isinstance(v, str) and v and '\x00' not in v for v in gate['argv']), 'Missing actual argv')
    _absolute(gate['cwd'])
    log_path = _owned(gate['log'], root)
    log = log_path.read_text()
    _require(isinstance(gate['sentinel'], str) and gate['sentinel'].strip() and gate['sentinel'] in log,
             'Native log lacks its actual gate sentinel')
    pure_terminal = name == 'pure_resources' and gate['role'] == 'terminal'
    if not pure_terminal:
        _require(re.search(r'(?:^|\n)(?:exit|exit_code|actual_exit)=0(?:\s|$)', log), 'Native log lacks actual exit0')
    if gate['underlying_receipt'] is None:
        _require(name == 'core', 'Producer command receipt required')
    else:
        command_path = _owned(gate['underlying_receipt'], root)
        command = _json(command_path)
        exit_code = command.get('exit_code', command.get('actual_exit'))
        _require(type(exit_code) is int and exit_code == 0 and
                 command.get('timed_out', False) is False and command.get('expected_exit', 0) == 0 and
                 command.get('command', command.get('argv')) == gate['argv'], 'Underlying native command differs')
        if pure_terminal:
            _require(command_path.name == 'terminal-command.json' and log_path == command_path.parent / 'terminal.stdout.log' and
                     terminal is not None and _json(log_path) == terminal and terminal.get('status') == 'PASS' and
                     terminal.get('interpreter_version') == '3.13.13' and
                     any(row['path'] == gate['argv'][0] and row['sha256'] == command.get('interpreter_sha256')
                         for row in gate['artifacts']), 'Actual pure terminal stdout/interpreter binding differs')
        else:
            _require(command.get('log_sha256') == gate['log']['sha256'], 'Underlying native command/log differs')
    _require(isinstance(gate['artifacts'], list) and isinstance(gate['audits'], list), 'Native artifact/audit list required')
    if gate['role'] in ('runtime', 'embedding', 'terminal'):
        _require(gate['artifacts'] and gate['audits'], 'Signed actual runtime artifact/audit missing')
        audits = [_json(_owned(row, root)) for row in gate['audits']]
        artifact_paths = []
        for artifact in gate['artifacts']:
            path = _file(artifact)
            # The pure terminal interpreter is an explicit separately produced prerequisite.
            _require(name == 'pure_resources' or path.is_relative_to(root), 'Runtime artifact outside its producer root')
            _signed_elf(path)
            _require(any(_audit_hash(value, artifact['sha256']) for value in audits), 'Artifact hash/signature has no current audit')
            artifact_paths.append(str(path))
        _require(gate['argv'][0] in artifact_paths, 'Gate argv does not execute the hash-bound native artifact')
    else:
        _require(not gate['artifacts'] and not gate['audits'], 'Non-runtime gate must use empty artifact/audit lists')
    return gate['role']


def _producer(name, reference, root, prefix, seal):
    path = _owned(reference, root)
    _require(path == root / _MARKERS[name], 'Wrong actual producer ownership marker')
    marker = _json(path)
    key = 'sealed_input_lock_sha256' if name == 'core' else 'inputs_lock_sha256' if name in ('base', 'python_native') else 'input_lock_sha256'
    if name == 'base':
        _keys(marker, {'kind', key, 'prefix'})
        _require(marker['kind'] == 'independent-source-built-Base', 'Old/prototype Base producer refused')
    elif name == 'python_native':
        _keys(marker, {'schema', 'owner', key})
        _require(type(marker['schema']) is int and marker['schema'] == 1, 'Invalid native Python ownership schema')
    elif name == 'pure_resources':
        _keys(marker, {'schema_version', 'owner', key, 'source_lock_sha256', 'cache', 'resources'})
        _require(marker['owner'] == _RECIPE_DIRS[name] + '/builder.py' and marker['source_lock_sha256'] ==
                 '27aeac5e6fd312c672d09d31c6d937d3fa0bb9b77080017617ff75d4e444536d',
                 'Wrong pure wrapper producer entry/original source lock')
    elif name == 'vulkan':
        _keys(marker, {'kind', key, 'prefix', 'tmp_dir', 'sources_root', 'archives_root'})
        _require(marker['kind'] == 'portable-vulkan-builder-private-root' and
                 marker['sources_root'] == str(root / 'sources') and marker['archives_root'] == str(root / 'archives'),
                 'Wrong new Vulkan producer layout')
    else:
        _keys(marker, {'schema_version', key, 'prefix', 'tmp_dir'})
    _require(marker.get(key) == seal, 'Producer ownership input seal differs')
    if name == 'python_native':
        _require(marker.get('owner') == _NAMESPACES[name] and prefix == root / 'runtime', 'Python runtime producer identity differs')
    else:
        key = 'resources' if name == 'pure_resources' else 'prefix'
        _require(marker.get(key) == str(prefix), 'Producer marker does not own the selected prefix')
    return marker


def _underlying(name, full, receipts, prefix, rows, seal, root, source_locks):
    locks = {Path(row['path']).name: row['sha256'] for row in source_locks}
    if name in ('python_native', 'pure_resources'):
        _require(full.get('source_lock_sha256') == locks['sources.lock.json'], 'Actual native/terminal original source lock differs')
    if name == 'python_native':
        _require(full.get('tool_lock_sha256') == locks['tools.lock.json'], 'Actual Python build-tool provenance differs')
    values = {key: _json(_owned(row, root) if key != 'resources.json' else _file(row)) for key, row in receipts.items()}
    for key, row in receipts.items():
        canonical(key)
        expected = prefix / key if key == 'resources.json' else root / key
        _require(Path(row['path']) == expected, 'Receipt selector/path mismatch: ' + key)
    if name in _FULL:
        key, sentinel = _FULL[name]
        _require(full.get(key) == sentinel, 'Underlying recipe has no actual new full acceptance')
        if name != 'base':
            _require(full.get('input_lock_sha256') == seal, 'Underlying full receipt is for another source seal')
        if name in ('geometry', 'volume', 'color'):
            _require(full.get('new_full_native_acceptance') is True, 'New source-built native full not completed')
        if name == 'core':
            _require(full.get('new_builder_accepted_on_this_host') is True, 'Core new builder not accepted')
        needed = {'native-validation.json', 'native-artifact-audit.json', 'migration-validation.json', 'metadata-normalization.json'} if name == 'base' else {'acceptance.json', 'artifacts.json', 'migration.json'}
        if name == 'core':
            needed |= {'sources.json', 'prerequisites.json', 'metadata-normalization.json'}
            _require(full.get('receipts') == [str(root / key) for key in
                     ('sources.json', 'prerequisites.json', 'acceptance.json', 'artifacts.json', 'migration.json')],
                     'Core aggregate must select the actual producer children')
        if name == 'vulkan':
            needed.add('metadata-normalization.json')
        _require(needed <= set(values), 'Required concrete producer child receipts missing')
        acceptance = values['native-validation.json' if name == 'base' else 'acceptance.json']
        if name == 'base':
            _require(acceptance.get('new_independent_native') == 'PASS' and
                     set(acceptance.get('CMAKE_PC_consumers', {})) == {'CMAKE', 'PC'}, 'Base native consumers missing')
        elif name == 'vulkan':
            # This recipe's genuine child has consumers/scope, without a result flag.
            _require(isinstance(acceptance.get('consumers'), list) and len(acceptance['consumers']) == 2 and
                     acceptance.get('scope') == 'Real native library APIs and loader facts; no GPU pixels/WSI',
                     'Vulkan actual CMAKE/PC native consumers missing')
        else:
            _require(isinstance(acceptance.get('result'), str) and acceptance['result'].startswith('PASS '),
                     'Native child acceptance incomplete')
        migration = values['migration-validation.json' if name == 'base' else 'migration.json']
        _require(isinstance(migration.get('result'), str) and migration['result'].startswith('PASS '), 'Actual migration child missing')
        audit = values['native-artifact-audit.json' if name == 'base' else 'artifacts.json']
        archive_rows = audit.get('native_archive_audit' if name == 'base' else 'archives', [])
        actual_archives = {row['path']: row['sha256'] for row in rows if row['kind'] == 'file' and row['path'].endswith('.a')}
        for path, digest in actual_archives.items():
            _require(any(row.get('sha256') == digest and Path(row.get('path', row.get('file', ''))).name == Path(path).name
                         for row in archive_rows), 'Current archive lacks actual native producer evidence: ' + path)
        if name in ('geometry', 'volume', 'color', 'vulkan'):
            _require('prefix-files.json' in values, 'Original producer prefix inventory missing')
            original = values['prefix-files.json']
            _require(original.get('input_lock_sha256') == seal, 'Producer prefix inventory input drift')
            _compare_original_inventory(original['files'], rows)
        if name == 'gltf':
            _require('prefix-seal.json' in values and full.get('prefix_seal_sha256') == receipts['prefix-seal.json']['sha256'], 'glTF prefix seal not aggregate-bound')
            _compare_original_inventory(values['prefix-seal.json']['files'], rows)
            for key in ('acceptance.json', 'artifacts.json', 'migration.json'):
                _require(full.get('receipts', {}).get(key) == receipts[key]['sha256'], 'glTF native child receipt changed')
            _require(audit.get('acceptance_sha256') == receipts['acceptance.json']['sha256'] and
                     audit.get('prefix_seal_sha256') == receipts['prefix-seal.json']['sha256'] and
                     audit.get('input_lock_sha256') == seal, 'glTF artifact lineage differs')
    elif name == 'python_native':
        _require(full.get('kind') == 'python-native-runtime-source-replay' and full.get('inputs_lock_sha256') == seal and
                 full.get('actual_fresh_native_and_selected_pure8') == 'PASS only after actual commands above' and
                 full.get('counts') == {'runtime_ELF': 93, 'HAP_DSO': 92, 'native_map': 90, 'pure_distributions': 8}, 'Fresh Python93/pure8 pipeline missing')
        _compare_original_inventory(full['runtime_inventory'], rows)
        _require(full.get('fresh_receipts'), 'Python actual native children missing')
        for row in full['fresh_receipts']:
            _require(row['path'] in receipts and row['sha256'] == receipts[row['path']]['sha256'], 'Python native child SHA differs')
    else:
        _require(full.get('stage') in ('full', 'audit') and full.get('status') == 'PASS' and
                 full.get('input_lock_sha256') == seal and full.get('terminal_native_checked') is True and
                 full.get('terminal', {}).get('status') == 'PASS' and full['terminal'].get('interpreter_version') == '3.13.13',
                 'Pure data-only receipt cannot satisfy independent actual terminal gate')
        _require({'current/terminal.json', 'resources.json'} <= set(values), 'Pure current terminal/resource manifest missing')
        pointer = values['current/terminal.json']
        _require(pointer.get('terminal_native_checked') is True and pointer.get('input_lock_sha256') == seal and
                 pointer.get('receipt_sha256') == sha(root / canonical(pointer['attempt']) / 'result.json'), 'Pure terminal pointer not bound to actual attempt')
        resource = values['resources.json']
        _require(resource.get('kind') == 'pure-python-resources-only' and resource.get('lock_sha256') == full.get('source_lock_sha256') and
                 resource.get('site_tree_sha256') == full['terminal'].get('site_tree_sha256') and
                 full.get('resource_manifest', {}).get('sha256') == receipts['resources.json']['sha256'], 'Pure selected terminal payload differs')
        site = canonical(resource['site_relative'])
        actual = [{k: row[k] for k in ('path', 'size', 'sha256')} for row in rows
                  if row['kind'] == 'file' and row['path'].startswith(site + '/')]
        for row in actual:
            row['path'] = row['path'][len(site) + 1:]
        _require(actual == resource['site_inventory'] and len(actual) == 193, 'Pure193 current payload changed')
    return values


def _compare_original_inventory(original, rows):
    indexed = {row['path']: row for row in rows}
    _require(isinstance(original, list) and len(original) == len(indexed), 'Original native prefix inventory incomplete')
    seen = set()
    for row in original:
        rel = canonical(row['path'])
        _require(rel in indexed and rel not in seen, 'Original inventory path differs/duplicates')
        seen.add(rel)
        current = indexed[rel]
        if current['kind'] == 'file':
            _require(type(row.get('size')) is int and row.get('sha256') == current['sha256'] and
                     row.get('size') == current['size'], 'Current native prefix bytes differ')
        else:
            _require(row.get('symlink', row.get('target')) == current['target'], 'Current installed link literal differs')
            if 'target_sha256' in row:
                _require(row['target_sha256'] == current['target_sha256'] and row.get('target_size') == current['target_size'], 'Current installed link target differs')


def _verify_dependencies(source: Path, bundle_path: Path, expected_sha256: str, allow_pending=False) -> dict:
    """Validate current declarations; reject unavailable native proof by default."""
    _require(type(allow_pending) is bool, 'allow_pending must be boolean')
    source = _absolute(str(source))
    _require(source.is_dir(), 'Source root missing')
    bundle_path = _absolute(str(bundle_path))
    digest = sha(bundle_path)
    _require(digest == _hash(expected_sha256), 'External caller bundle SHA differs')
    bundle = _json(bundle_path)
    _keys(bundle, {'schema_version', 'kind', 'canonical_helper', 'dependencies'}, {'supplemental_inputs'})
    _require(bundle['schema_version'] == 1 and bundle['kind'] == 'editor-dependency-prefix-contract', 'Wrong dependency contract schema')
    helper = bundle['canonical_helper']
    _require(helper['path'] == _HELPER and helper['sha256'] == _HELPER_SHA, 'Canonical existing volume helper substituted')
    _file(helper, source)
    _require(isinstance(bundle['dependencies'], dict) and set(bundle['dependencies']) == set(DEPENDENCIES), 'Require all nine distinct dependency declarations')
    gaps, records, prefixes = [], {}, {}
    for name in DEPENDENCIES:
        declaration = bundle['dependencies'][name]
        _keys(declaration, {'prefix', 'status', 'recipe'}, _READY)
        _require(declaration['status'] in ('PENDING_NATIVE', 'PASS_NATIVE'), 'Unknown dependency gate state')
        prefix = _absolute(declaration['prefix'])
        _require(not (prefix.is_relative_to(source) or source.is_relative_to(prefix)), 'Prefix overlaps immutable source')
        seal = _recipe(source, name, declaration['recipe'])
        record = {'prefix': str(prefix), 'input_lock_sha256': seal,
                  'entry_sha256': declaration['recipe']['entry']['sha256'], 'status': declaration['status']}
        if declaration['status'] == 'PENDING_NATIVE':
            gaps.append({'dependency': name, 'reason': 'Fresh source-built native acceptance pending' if name != 'pure_resources' else 'Independent actual terminal pure gate pending'})
        missing = sorted(_READY - set(declaration))
        if missing:
            gaps.append({'dependency': name, 'reason': 'Missing native evidence fields: ' + ', '.join(missing)})
        root = _absolute(declaration['build_root']) if 'build_root' in declaration else None
        producer_sha = declaration.get('producer', {}).get('sha256')
        if root is not None:
            _require(not (root.is_relative_to(source) or source.is_relative_to(root)), 'Producer root overlaps source')
            if 'producer' in declaration:
                _producer(name, declaration['producer'], root, prefix, seal)
            for key in ('inventory', 'metadata', 'adapter', 'native_receipt'):
                if key in declaration:
                    _owned(declaration[key], root)
            for row in declaration.get('receipts', {}).values():
                _file(row)
            for row in declaration.get('gates', []):
                _owned(row, root)
        elif any(key in declaration for key in ('producer', 'inventory', 'metadata', 'adapter', 'native_receipt', 'receipts', 'gates')):
            raise ValueError('Available evidence requires its actual build_root')
        rows = _inventory(prefix) if prefix.exists() else None
        if rows is None:
            gaps.append({'dependency': name, 'reason': 'Declared current prefix does not exist'})
        if 'inventory' in declaration:
            _require(root is not None and producer_sha is not None and rows is not None, 'Inventory lacks current producer/prefix')
            saved = _document(declaration['inventory'], root, 'editor-prefix-inventory', seal, producer_sha)
            _require(saved == rows, 'Immutable prefix inventory file/link set differs')
        if 'metadata' in declaration:
            _require(root is not None and producer_sha is not None and rows is not None, 'Metadata lacks current producer/prefix')
            saved = _document(declaration['metadata'], root, 'editor-prefix-metadata', seal, producer_sha)
            record['producer_metadata'] = _metadata(prefix, rows, saved, source, root, name, declaration, gaps)
        if 'interfaces' in declaration:
            _require(rows is not None, 'Declared interfaces have no current prefix')
            _interfaces(prefix, rows, declaration['interfaces'], name)
        if not missing and rows is not None:
            adapter = _json(_owned(declaration['adapter'], root))
            _keys(adapter, {'schema_version', 'kind', 'dependency', 'prefix', 'entry_sha256', 'input_lock_sha256', 'source_locks',
                            'producer_sha256', 'inventory_sha256', 'metadata_sha256', 'native_receipt_sha256', 'receipts', 'gates'})
            expected = {'schema_version': 1, 'kind': 'editor-native-dependency-adapter', 'dependency': name,
                        'prefix': str(prefix), 'entry_sha256': record['entry_sha256'], 'input_lock_sha256': seal,
                        'source_locks': declaration['recipe']['source_locks'], 'producer_sha256': producer_sha,
                        'inventory_sha256': declaration['inventory']['sha256'], 'metadata_sha256': declaration['metadata']['sha256'],
                        'native_receipt_sha256': declaration['native_receipt']['sha256'],
                        'receipts': {key: row['sha256'] for key, row in declaration['receipts'].items()},
                        'gates': [row['sha256'] for row in declaration['gates']]}
            _require(adapter == expected, 'External adapter provenance chain differs')
            full_path = _owned(declaration['native_receipt'], root)
            if name in ('core', 'geometry', 'volume', 'color', 'vulkan', 'gltf'):
                _require(full_path == root / 'full-native-acceptance.json', 'Wrong actual native full receipt selector')
            elif name == 'python_native':
                _require(full_path == root / 'runtime-manifest.json', 'Wrong actual Python runtime manifest selector')
            full = _json(full_path)
            roles = {_gate(row, name, root, seal, producer_sha,
                           full.get('terminal') if name == 'pure_resources' else None)
                     for row in declaration['gates']}
            if declaration['status'] == 'PASS_NATIVE':
                _underlying(name, full, declaration['receipts'], prefix, rows, seal, root,
                            declaration['recipe']['source_locks'])
                if name == 'pure_resources':
                    pointer = _json(_owned(declaration['receipts']['current/terminal.json'], root))
                    _require(full_path == root / canonical(pointer['attempt']) / 'result.json' and
                             declaration['native_receipt']['sha256'] == pointer['receipt_sha256'], 'Pure terminal selected attempt differs')
                    for gate_reference in declaration['gates']:
                        gate = _json(Path(gate_reference['path']))
                        if gate['role'] == 'terminal':
                            _require(gate['underlying_receipt']['path'] == str(full_path.parent / 'terminal-command.json'),
                                     'Pure terminal command does not belong to the selected attempt')
                required_roles = {'terminal'} if name == 'pure_resources' else {'full', 'configure', 'build', 'install', 'runtime', 'migration'}
                if name == 'python_native':
                    required_roles.add('embedding')
                _require(required_roles <= roles, 'Actual native command roles missing: ' + repr(sorted(required_roles - roles)))
            record.update(native_receipt_sha256=declaration['native_receipt']['sha256'],
                          producer_sha256=producer_sha, prefix_files=len(rows), gates=sorted(roles),
                          metadata_sha256=declaration['metadata']['sha256'])
        records[name] = record
        prefixes[name] = str(prefix)
    if records['core']['status'] == 'PASS_NATIVE' and 'native_receipt_sha256' in records['core']:
        if 'native_receipt_sha256' not in records['volume'] or records['volume']['status'] != 'PASS_NATIVE':
            gaps.append({'dependency': 'core', 'reason': 'Core prerequisite lacks independently accepted canonical volume TBB producer'})
        else:
            core = bundle['dependencies']['core']
            prerequisite = _json(Path(core['receipts']['prerequisites.json']['path']))
            _require(prerequisite.get('tbb_prefix_explicit') == prefixes['volume'], 'Core TBB prerequisite must be the canonical new volume prefix')
            volume_files = _json(Path(bundle['dependencies']['volume']['inventory']['path']))['files']
            indexed = {row['path']: row for row in volume_files}
            for library in ('lib/libtbb.a', 'lib/libtbbmalloc.a'):
                _require(any(row.get('path') == str(Path(prefixes['volume']) / library) and
                             row.get('sha256') == indexed[library]['sha256']
                             for row in prerequisite.get('tbb_archives', [])), 'Core canonical TBB archive provenance differs')
            records['core']['tbb_producer_input_lock_sha256'] = records['volume']['input_lock_sha256']
            records['core']['tbb_producer_native_receipt_sha256'] = records['volume']['native_receipt_sha256']
    supplemental = bundle.get('supplemental_inputs', {})
    _keys(supplemental, set())  # SSE2NEON materialization belongs to the editor source stage.
    _require(len(set(prefixes.values())) == len(prefixes), 'Dependencies cannot silently share an adopted prefix')
    for name, left in prefixes.items():
        for other, right in prefixes.items():
            _require(name == other or not Path(left).is_relative_to(Path(right)), 'Dependency prefixes overlap')
    if gaps and not allow_pending:
        raise ValueError('Dependency native gates blocked: ' + '; '.join(row['dependency'] + ': ' + row['reason'] for row in gaps))
    return {'schema_version': 1, 'kind': 'verified-editor-dependency-prefix-contract',
            'status': 'PREPARED_NOT_RUN' if gaps else 'PASS_NATIVE', 'bundle_sha256': digest,
            'prefixes': prefixes, 'records': records, 'gaps': gaps, 'supplemental_inputs': supplemental,
            'canonical_selection': {'prefix': prefixes['volume'], 'helper': _HELPER,
                                    'helper_sha256': _HELPER_SHA,
                                    'targets': {target: {'library': library, 'requires': requires, 'definitions': definitions}
                                                for target, (library, requires, definitions) in _VOLUME_TARGETS.items()},
                                    'validated': not gaps}}


def verify_dependencies(source: Path, bundle_path: Path, expected_sha256: str, allow_pending=False) -> dict:
    """Verify a sealed caller contract without writing or invoking native tools.

    Malformed declarations and byte/provenance drift raise ValueError; unavailable
    files may raise OSError. Pending native/source evidence becomes explicit gaps
    only when allow_pending is true. PASS_NATIVE describes dependency evidence,
    never editor postlink, bpy, GUI or HAP acceptance.
    """
    try:
        return _verify_dependencies(source, bundle_path, expected_sha256, allow_pending)
    except (KeyError, TypeError, AttributeError, UnicodeError, struct.error) as error:
        raise ValueError('Malformed dependency evidence: ' + str(error)) from error
