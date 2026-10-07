# SPDX-License-Identifier: GPL-2.0-or-later
"""Retained no-follow directory IO for a selected cooperating toolkit owner.

Records are materials, never authority. The caller supplies a separately selected
owner marker; only the finite owner capture/build sinks are writable. Not a
sandbox against arbitrary same-UID programs or a cryptographic origin proof.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import stat
import tempfile

NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
ANCESTOR = getattr(os, 'O_PATH', 0) | os.O_DIRECTORY | NOFOLLOW | os.O_CLOEXEC
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | os.O_CLOEXEC


def require(condition, reason):
    if not condition: raise ValueError(reason)


def absolute(path):
    text = str(path); result = Path(text)
    require(result.is_absolute() and result.as_posix() == text and '..' not in result.parts and
            not any(ord(c) < 32 for c in text), 'Canonical absolute route required')
    return result


def identity(info): return [info.st_dev, info.st_ino]


def regular(info, mutable=False):
    require(stat.S_ISREG(info.st_mode) and (not mutable or info.st_nlink == 1), 'Regular single-link mutable leaf required')


def open_directory(path):
    path = absolute(path); require(NOFOLLOW and getattr(os, 'O_PATH', 0), 'Actual O_PATH/no-follow required')
    fd = os.open('/', ANCESTOR)
    try:
        for part in path.parts[1:]:
            child = os.open(part, ANCESTOR, dir_fd=fd); os.close(fd); fd = child
        return fd
    except BaseException:
        os.close(fd); raise


def readonly_aliases(path):
    import posixpath
    pending = list(absolute(path).parts[1:]); prefix = Path('/'); links = []; count = 0
    fd = os.open('/', ANCESTOR)
    try:
        while pending:
            part = pending.pop(0); info = os.stat(part, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                count += 1; require(count <= 40, 'Readonly alias cycle/depth')
                target = os.readlink(part, dir_fd=fd)
                require(identity(os.stat(part, dir_fd=fd, follow_symlinks=False)) == identity(info), 'Readonly alias replaced during read')
                links.append({'path': str(prefix / part), 'target': target, 'identity': identity(info)})
                normal_seen = False
                for component in target.split('/'):
                    if component == '..': require(not normal_seen, 'Readonly alias non-leading parent traversal unsupported')
                    elif component not in ('', '.'): normal_seen = True
                combined = target if target.startswith('/') else str(prefix / target)
                combined = posixpath.normpath(combined)
                pending = list(absolute(combined).parts[1:]) + pending; prefix = Path('/')
                os.close(fd); fd = os.open('/', ANCESTOR)
            else:
                prefix = prefix / part
                if pending:
                    child = os.open(part, ANCESTOR, dir_fd=fd); os.close(fd); fd = child
        return prefix, links
    finally: os.close(fd)


def read_original(path, *, alias=False, limit=1024 * 1024 * 1024):
    requested = absolute(path); resolved = requested; aliases = []
    if alias:
        # Selected SDK/tool aliases are readonly inputs, never mutable sinks.
        resolved, aliases = readonly_aliases(requested)
    parent = open_directory(resolved.parent)
    try:
        fd = os.open(resolved.name, os.O_RDONLY | NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            before = os.fstat(fd); regular(before); require(before.st_size <= limit, 'Original input too large')
            blocks = []; size = 0
            while block := os.read(fd, 1024 * 1024):
                blocks.append(block); size += len(block); require(size <= limit, 'Original input exceeded bound')
            after = os.fstat(fd); current = os.stat(resolved.name, dir_fd=parent, follow_symlinks=False)
            require(identity(before) == identity(after) == identity(current) and
                    before.st_size == after.st_size == size and before.st_mtime_ns == after.st_mtime_ns,
                    'Original input inode/bytes changed during read')
            if alias:
                after_resolved, after_aliases = readonly_aliases(requested)
                require(after_resolved == resolved and after_aliases == aliases, 'Selected readonly alias changed during read')
            data = b''.join(blocks)
            return data, {'path': str(requested), 'resolved_path': str(resolved), 'size': size,
                          'sha256': hashlib.sha256(data).hexdigest(), 'identity': identity(before), 'readonly_aliases': aliases}
        finally: os.close(fd)
    finally: os.close(parent)


def verify_reference(reference):
    require(isinstance(reference, dict) and {'path', 'size', 'sha256'} <= set(reference), 'Independent reference absent')
    data, observed = read_original(reference['path'])
    require(type(reference['size']) is int and observed['size'] == reference['size'] and observed['sha256'] == reference['sha256'],
            'Independent selected reference differs')
    return data


class Directory:
    def __init__(self, root, fd=None):
        self.root = absolute(root); self.fd = open_directory(root) if fd is None else os.dup(fd)
        require(stat.S_ISDIR(os.fstat(self.fd).st_mode), 'Directory authority required')
        self.identity = identity(os.fstat(self.fd)); self.closed = False

    def check(self):
        require(not self.closed, 'Closed directory authority')
        probe = open_directory(self.root)
        try: require(identity(os.fstat(probe)) == self.identity, 'Borrowed/replaced selected owner root')
        finally: os.close(probe)

    def close(self):
        if not self.closed: os.close(self.fd); self.closed = True

    def parts(self, path):
        path = absolute(path); relative = path.relative_to(self.root).as_posix()
        require(relative != '.' and all(p not in ('', '.', '..') for p in relative.split('/')), 'Cannot mutate directory root as leaf')
        return relative.split('/')

    @contextmanager
    def parent(self, path, create=False):
        self.check(); parts = self.parts(path); fd = os.open('.', DIRECTORY, dir_fd=self.fd)
        try:
            for part in parts[:-1]:
                try: child = os.open(part, DIRECTORY, dir_fd=fd)
                except FileNotFoundError:
                    require(create, 'Missing selected sink ancestor'); os.mkdir(part, 0o700, dir_fd=fd)
                    child = os.open(part, DIRECTORY, dir_fd=fd)
                os.close(fd); fd = child
            yield fd, parts[-1]
        finally: os.close(fd)

    def mkdir(self, path):
        with self.parent(path, create=True) as (fd, name):
            os.mkdir(name, 0o700, dir_fd=fd)
            child = os.open(name, ANCESTOR, dir_fd=fd)
            try: return Directory(path, child)
            finally: os.close(child)

    def write(self, path, data, exclusive=True):
        with self.parent(path, create=True) as (parent, name):
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False); regular(info, True)
                require(not exclusive, 'Refuse overwrite of capture material')
            except FileNotFoundError: info = None
            flags = os.O_WRONLY | os.O_CREAT | NOFOLLOW | os.O_CLOEXEC | (os.O_EXCL if exclusive else 0)
            fd = os.open(name, flags, 0o600, dir_fd=parent)
            try:
                current = os.fstat(fd); regular(current, True)
                require(info is None or identity(info) == identity(current), 'Mutable sink replaced at open')
                if not exclusive: os.ftruncate(fd, 0)
                with os.fdopen(fd, 'wb', closefd=False) as stream: stream.write(data); stream.flush(); os.fsync(fd)
                require(identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity(current), 'Mutable sink replaced during write')
                return {'path': str(path), 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'identity': identity(current)}
            finally: os.close(fd)

    def dump(self, path, value):
        return self.write(path, (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode())

    def unlink(self, path):
        with self.parent(path) as (parent, name):
            regular(os.stat(name, dir_fd=parent, follow_symlinks=False), True); os.unlink(name, dir_fd=parent)

    def move_from(self, source_dir, source, destination):
        # Retain both original parents until rename/copy/unlink is complete.
        with source_dir.parent(source) as (left, src), self.parent(destination, create=True) as (right, dst):
            original = os.stat(src, dir_fd=left, follow_symlinks=False); regular(original, True)
            try: os.stat(dst, dir_fd=right, follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise ValueError('Refuse existing ABI publication sink')
            try: os.rename(src, dst, src_dir_fd=left, dst_dir_fd=right); kind = 'rename'
            except OSError as error:
                import errno
                if error.errno != errno.EXDEV: raise
                infd = os.open(src, os.O_RDONLY | NOFOLLOW, dir_fd=left)
                try:
                    outfd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o700, dir_fd=right)
                    try:
                        require(identity(os.fstat(infd)) == identity(original), 'Original transfer source replaced')
                        with os.fdopen(infd, 'rb', closefd=False) as inp, os.fdopen(outfd, 'wb', closefd=False) as out:
                            while block := inp.read(1024 * 1024): out.write(block)
                            out.flush(); os.fsync(outfd)
                        regular(os.fstat(outfd), True)
                        require(identity(os.stat(src, dir_fd=left, follow_symlinks=False)) == identity(original), 'Source changed before unlink')
                        os.unlink(src, dir_fd=left); kind = 'copyfile_then_unlink'
                    finally: os.close(outfd)
                finally: os.close(infd)
            regular(os.stat(dst, dir_fd=right, follow_symlinks=False), True)
            return kind


def selected_owner(root, expected_marker):
    directory = Directory(root)
    try:
        require(expected_marker['path'] == str(directory.root / 'owner.json'), 'Selected owner marker path differs')
        value = json.loads(verify_reference(expected_marker))
        require(value['kind'] == 'provided-native-ohos-toolkit-owner' and value['root'] == str(directory.root), 'Independent toolkit owner differs')
        return directory, value
    except BaseException: directory.close(); raise


@contextmanager
def temporary(tmp):
    tmp = absolute(tmp); require(tmp == absolute(os.environ['TMPDIR']), 'Use actual managed TMPDIR')
    with tempfile.TemporaryDirectory(prefix='toolkit-abi-sign-', dir=tmp) as name:
        directory = Directory(Path(name))
        try: yield directory
        finally: directory.close()
