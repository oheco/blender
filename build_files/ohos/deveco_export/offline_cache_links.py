# SPDX-License-Identifier: GPL-2.0-or-later
"""Opted user-local Hvigor npm filelinks only. No JS, tool or native execution.

FD-relative IO prevents following link directories during this operation. It is
not a sandbox against concurrent processes of the same UID. Generic source,
SDK, support and regular-only cache guards do not use this module.
"""
from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
from common import load, object_digest, relative, require

KIND = 'sealed-hvigor-offline-cache-with-npm-bin-links-v1'
PROFILE = 'hvigor-npm-bin-filelinks-v1'
LOCAL_SCOPE = 'user-local-external-tools-not-public-redistribution'
_SEARCH = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _absolute(value):
    text = str(value)
    require(text.startswith('/') and os.path.normpath(text) == text and
            '\\' not in text and not any(ord(c) < 32 or ord(c) == 127 for c in text),
            'Canonical absolute cache/owned path required')
    return Path(text)


def _metadata(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_uid, value.st_gid)


def _open_absolute(value):
    path = _absolute(value)
    # Ancestors need search permission, not permission to list protected '/'.
    # The selected final cache directory is still opened read-only for listing.
    fd = os.open('/', _SEARCH)
    try:
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            flags = _DIR if index == len(parts) - 1 else _SEARCH
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _parent(root_fd, name):
    parts = relative(name).split('/')
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _open_regular(parent_fd, name):
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode), 'Cache target must be a regular file: ' + name)
    fd = os.open(name, _FILE, dir_fd=parent_fd)
    try:
        require(_metadata(before) == _metadata(os.fstat(fd)), 'Cache file changed during open')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _hash_fd(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    value = hashlib.sha256()
    for block in iter(lambda: os.read(fd, 1024 * 1024), b''):
        value.update(block)
    return value.hexdigest()


def _raw_target(target):
    require(isinstance(target, str) and target.startswith('../'),
            'Exact single-parent relative filelink target required')
    # One npm ../ is intentional. Additional traversal, empty/dot/backslash or
    # absolute components fail relative(); never resolve through filesystem links.
    return relative(target[3:])


def _layout(manifest):
    require(manifest.get('kind') == KIND and type(manifest.get('schema')) is int and manifest['schema'] == 1 and
            manifest.get('label') == 'hvigor' and manifest.get('cache_profile') == PROFILE and
            manifest.get('external_tool_scope') == LOCAL_SCOPE and
            manifest.get('network_at_build_time') is False,
            'Explicit opted user-local Hvigor filelink contract required')
    files, links, directories = manifest.get('files'), manifest.get('symlinks'), manifest.get('directories')
    require(isinstance(files, list) and 0 < len(files) <= 25000 and
            isinstance(links, list) and len(links) <= 64 and
            isinstance(directories, list) and len(directories) <= 4096,
            'Finite complete file/link/directory inventories required')
    regulars, symlinks, dirs, names = {}, {}, set(), {}
    def member(name):
        name = relative(name)
        require(len(name.split('/')) <= 128 and name.casefold() not in names,
                'Duplicate/case-colliding cache member: ' + name)
        names[name.casefold()] = name
        return name
    for row in files:
        require(isinstance(row, dict) and row.get('kind') == 'file', 'Typed regular cache row required')
        name = member(row['path'])
        require(type(row.get('size')) is int and row['size'] >= 0 and
                type(row.get('mode')) is int and 0 <= row['mode'] <= 0o777 and
                isinstance(row.get('sha256'), str) and len(row['sha256']) == 64 and
                all(c in '0123456789abcdef' for c in row['sha256']), 'Invalid regular cache seal')
        regulars[name] = row
    for name in directories:
        dirs.add(member(name))
    for row in links:
        require(isinstance(row, dict) and row.get('kind') == 'symlink', 'Typed npm filelink row required')
        name = member(row['path'])
        parts = name.split('/')
        require(len(parts) >= 3 and parts[0] == 'node_modules' and parts[-3:-1] == ['node_modules', '.bin'],
                'Only declared node_modules/.bin filelinks are supported')
        rest = _raw_target(row.get('target'))
        terminal = (PurePosixPath(name).parent.parent / rest).as_posix()
        require(row.get('resolved_target') == terminal and terminal in regulars and
                type(row.get('target_size')) is int and
                row.get('target_sha256') == regulars[terminal]['sha256'] and
                row.get('target_size') == regulars[terminal]['size'],
                'Filelink must terminate directly at its declared sealed regular file')
        symlinks[name] = row
    for name in list(regulars) + list(symlinks) + list(dirs):
        parents = list(PurePosixPath(name).parents)[:-1]
        require(all(parent.as_posix() in dirs for parent in parents),
                'Missing or non-directory declared ancestor: ' + name)
    require(manifest.get('resolution_lock') in regulars and manifest.get('packages'),
            'Sealed resolution and complete package declarations required')
    return regulars, symlinks, dirs


def _scan(root_fd, regulars, symlinks, directories):
    observed, state, folded = set(), {}, set()
    expected = set(regulars) | set(symlinks) | directories
    def walk(fd, prefix):
        start = _metadata(os.fstat(fd))
        state[prefix] = start
        for leaf in sorted(os.listdir(fd)):
            name = relative(prefix + '/' + leaf if prefix else leaf)
            require(name in expected and name.casefold() not in folded,
                    'Undeclared/case-colliding cache object: ' + name)
            folded.add(name.casefold())
            before = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            metadata = _metadata(before)
            observed.add(name)
            if name in directories:
                require(stat.S_ISDIR(before.st_mode), 'Symlink/non-directory cache ancestor: ' + name)
                child = os.open(leaf, _DIR, dir_fd=fd)
                try:
                    require(_metadata(os.fstat(child)) == metadata, 'Directory changed during open')
                    walk(child, name)
                finally:
                    os.close(child)
            elif name in regulars:
                row = regulars[name]
                file_fd = _open_regular(fd, leaf)
                try:
                    require(before.st_size == row['size'] and
                            stat.S_IMODE(before.st_mode) == row['mode'] and
                            _hash_fd(file_fd) == row['sha256'], 'Regular cache bytes/mode differ: ' + name)
                    require(_metadata(os.fstat(file_fd)) == metadata, 'Regular cache changed while hashing')
                finally:
                    os.close(file_fd)
            else:
                require(stat.S_ISLNK(before.st_mode) and os.readlink(leaf, dir_fd=fd) == symlinks[name]['target'],
                        'Declared npm filelink type/raw target differs: ' + name)
            require(_metadata(os.stat(leaf, dir_fd=fd, follow_symlinks=False)) == metadata,
                    'Cache object changed during verification: ' + name)
            state[name] = metadata
        require(_metadata(os.fstat(fd)) == start, 'Cache directory changed during verification')
    walk(root_fd, '')
    require(observed == expected, 'Missing declared cache file/link/directory')
    # All terminal files were opened with O_NOFOLLOW through directory FDs. The
    # declared direct terminal has already passed the full regular-file seal.
    return state


class _CacheFile:
    def __init__(self, cache, name):
        self.cache, self.name = cache, name
        self.path = cache.root / name

    @contextmanager
    def open(self, mode='r', encoding=None):
        require(mode in ('r', 'rb'), 'Readonly sealed cache access only')
        fd = self.cache.open_file(self.name)
        binary = os.fdopen(fd, 'rb')
        stream = binary if mode == 'rb' else io.TextIOWrapper(binary, encoding=encoding or 'utf-8')
        try:
            yield stream
            require(_metadata(os.fstat(fd)) == self.cache.state[self.name], 'Cache read changed its input')
        finally:
            stream.close()


class VerifiedCache:
    """Hold a verified root FD until identity, copying and source recheck end."""
    def __init__(self, root, manifest):
        self.root = _absolute(root)
        self.manifest = manifest
        self.regulars, self.symlinks, self.directories = _layout(manifest)
        self.fd = _open_absolute(self.root)
        try:
            self.state = _scan(self.fd, self.regulars, self.symlinks, self.directories)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        os.close(self.fd)
        self.fd = None

    def file(self, name):
        require(name in self.regulars, 'Package/identity file is not a sealed regular cache member')
        return _CacheFile(self, name)

    def open_file(self, name):
        require(self.fd is not None and name in self.regulars, 'No active sealed regular cache FD')
        with _parent(self.fd, name) as (parent, leaf):
            fd = _open_regular(parent, leaf)
        try:
            require(_metadata(os.fstat(fd)) == self.state[name], 'Cache input changed since verification')
            return fd
        except BaseException:
            os.close(fd)
            raise

    def recheck(self):
        require(_scan(self.fd, self.regulars, self.symlinks, self.directories) == self.state,
                'Cache metadata or membership changed during consumption')
        bound = _open_absolute(self.root)
        try:
            require(_metadata(os.fstat(bound)) == self.state[''], 'Cache root pathname rebound')
        finally:
            os.close(bound)

    def package_metadata(self):
        for package in self.manifest['packages']:
            value = load(self.file(package['manifest']))
            require(value.get('name') == package['name'] and value.get('version') == package['version'],
                    'Sealed offline package metadata differs')

    def copy_to(self, destination, owned_root):
        destination, owned_root = _absolute(destination), _absolute(owned_root)
        require(destination.is_relative_to(owned_root) and destination != owned_root and
                not self.root.is_relative_to(owned_root) and not owned_root.is_relative_to(self.root),
                'Cache output must be a disjoint new subtree of its explicit owned root')
        private = [_absolute(os.environ[key]) for key in ('TMPDIR', 'XDG_CACHE_HOME') if os.environ.get(key)]
        require(any(owned_root.is_relative_to(root) and owned_root != root for root in private),
                'Owned staging root must be below private TMPDIR/cache')
        relative_dest = relative(destination.relative_to(owned_root).as_posix())
        owner_fd = _open_absolute(owned_root)
        target_fd = None
        try:
            owner_before = os.fstat(owner_fd)
            require(owner_before.st_uid == os.getuid(), 'Owned staging root belongs to another UID')
            parent_fd = os.dup(owner_fd)
            try:
                parts = relative_dest.split('/')
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    child = os.open(part, _DIR, dir_fd=parent_fd)
                    try:
                        require(os.fstat(child).st_uid == os.getuid(), 'Output parent is not caller-owned')
                    except BaseException:
                        os.close(child)
                        raise
                    os.close(parent_fd)
                    parent_fd = child
                os.mkdir(parts[-1], mode=0o700, dir_fd=parent_fd)
                target_fd = os.open(parts[-1], _DIR, dir_fd=parent_fd)
                target_identity = os.fstat(target_fd)
            finally:
                os.close(parent_fd)
            self.recheck()
            for name in sorted(self.directories, key=lambda value: (value.count('/'), value)):
                with _parent(target_fd, name) as (parent, leaf):
                    os.mkdir(leaf, mode=0o700, dir_fd=parent)
            for name, row in sorted(self.regulars.items()):
                incoming = self.open_file(name)
                outgoing = None
                try:
                    with _parent(target_fd, name) as (parent, leaf):
                        outgoing = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                           os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
                    value = hashlib.sha256()
                    for block in iter(lambda: os.read(incoming, 1024 * 1024), b''):
                        value.update(block)
                        rest = memoryview(block)
                        while rest:
                            written = os.write(outgoing, rest)
                            require(written > 0, 'Incomplete cache output write')
                            rest = rest[written:]
                    require(value.hexdigest() == row['sha256'] and
                            _metadata(os.fstat(incoming)) == self.state[name], 'Input changed while copying')
                    os.fchmod(outgoing, row['mode'])
                finally:
                    os.close(incoming)
                    if outgoing is not None:
                        os.close(outgoing)
            # Verify every copied regular target before creating any filelink.
            _scan(target_fd, self.regulars, {}, self.directories)
            for name, row in sorted(self.symlinks.items()):
                with _parent(target_fd, name) as (parent, leaf):
                    os.symlink(row['target'], leaf, dir_fd=parent)
            _scan(target_fd, self.regulars, self.symlinks, self.directories)
            self.recheck()
            bound = _open_absolute(destination)
            try:
                actual = os.fstat(bound)
                require((actual.st_dev, actual.st_ino) == (target_identity.st_dev, target_identity.st_ino),
                        'Owned output cache pathname rebound')
            finally:
                os.close(bound)
            bound_owner = _open_absolute(owned_root)
            try:
                actual = os.fstat(bound_owner)
                require((actual.st_dev, actual.st_ino) == (owner_before.st_dev, owner_before.st_ino),
                        'Owned staging root pathname rebound')
            finally:
                os.close(bound_owner)
        finally:
            if target_fd is not None:
                os.close(target_fd)
            os.close(owner_fd)
        return {'kind': KIND, 'cache_profile': PROFILE, 'destination': relative_dest,
                'external_tool_scope': LOCAL_SCOPE, 'manifest': self.manifest,
                'inventory_digest': object_digest(self.manifest), 'regular_files': len(self.regulars),
                'preserved_filelinks': len(self.symlinks), 'directories': len(self.directories),
                'scope': 'ACTUAL_FILE_IO_COPY_VERIFIED_NO_JS_OR_OFFLINE_BUILD_EXECUTION'}


def _selected_file(row):
    path = _absolute(row['path'])
    parent_fd = _open_absolute(path.parent)
    try:
        before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            require(row.get('kind') == 'symlink' and os.readlink(path.name, dir_fd=parent_fd) == row.get('target'),
                    'Selected Hvigor relative entry link requires explicit kind/raw target')
            target = _raw_target(row['target'])
            terminal = _absolute(path.parent.parent / target)
            require(row.get('resolved_path') == str(terminal), 'Selected entry terminal path differs')
            terminal_parent = _open_absolute(terminal.parent)
            try:
                fd = _open_regular(terminal_parent, terminal.name)
            finally:
                os.close(terminal_parent)
        else:
            require(row.get('kind', 'file') == 'file', 'Selected Hvigor regular entry kind differs')
            terminal = path
            fd = _open_regular(parent_fd, path.name)
        try:
            metadata = _metadata(os.fstat(fd))
            require(_hash_fd(fd) == row['sha256'] and
                    ('size' not in row or metadata[3] == row['size']) and
                    _metadata(os.fstat(fd)) == metadata and
                    _metadata(os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)) == _metadata(before),
                    'Selected Hvigor entry bytes/link metadata changed')
            return terminal, metadata
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def verify_selected_tool(row):
    terminal, metadata = _selected_file(row)
    return {'raw_path': row['path'], 'resolved_regular_path': str(terminal),
            'sha256': row['sha256'], 'size': metadata[3], 'kind': row.get('kind', 'file')}


def bind_selected_entry(row, sealed_entry):
    terminal, selected_metadata = _selected_file(row)
    actual = sealed_entry.path if isinstance(sealed_entry, _CacheFile) else Path(sealed_entry)
    require(terminal == _absolute(actual), 'Selected Hvigor terminal is not this cache bin/hvigor.cjs')
    if isinstance(sealed_entry, _CacheFile):
        fd = sealed_entry.cache.open_file(sealed_entry.name)
    else:
        parent_fd = _open_absolute(actual.parent)
        try:
            fd = _open_regular(parent_fd, actual.name)
        finally:
            os.close(parent_fd)
    try:
        require(_metadata(os.fstat(fd)) == selected_metadata, 'Selected Hvigor does not bind actual sealed entry')
    finally:
        os.close(fd)


def verify_copy_receipt(project, receipt):
    require(receipt.get('kind') == KIND and receipt.get('cache_profile') == PROFILE and
            receipt.get('destination') == 'offline/hvigor' and receipt.get('external_tool_scope') == LOCAL_SCOPE and
            receipt.get('inventory_digest') == object_digest(receipt['manifest']),
            'Wrong selected Hvigor cache copy receipt')
    with VerifiedCache(_absolute(project) / relative(receipt['destination']), receipt['manifest']) as cache:
        require(receipt['regular_files'] == len(cache.regulars) and
                receipt['preserved_filelinks'] == len(cache.symlinks) and
                receipt['directories'] == len(cache.directories), 'Cache copy counts changed')
        cache.recheck()
