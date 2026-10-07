# SPDX-License-Identifier: GPL-2.0-or-later
"""Emit an honest pending external contract; never invent native receipts."""
import argparse
import sys
sys.dont_write_bytecode = True
from pathlib import Path
from common import record, dump, absolute

RECIPES = {name: 'build_files/ohos/deps_' + suffix + '_sources' for name, suffix in {
    'base': 'base', 'core': 'core', 'geometry': 'geometry', 'volume': 'volume', 'color': 'color',
    'vulkan': 'vulkan', 'python_native': 'python_native', 'gltf': 'gltf', 'pure_resources': 'python_resources'}.items()}


def pending(repo, parent):
    def local(path):
        row = record(repo / path); row['path'] = path; return row
    dependencies = {}
    for name, namespace in RECIPES.items():
        directory = namespace if name in ('python_native', 'gltf') else namespace + '/builder'
        locks = [directory + '/sources.lock.json']
        if name == 'core':
            locks = [namespace + '/provenance.json']
        elif name == 'python_native':
            locks.append(namespace + '/tools.lock.json')
        elif name == 'gltf':
            locks.append(namespace + '/patches.lock.json')
        elif name == 'pure_resources':
            locks = [namespace + '/sources.lock.json']
        dependencies[name] = {'status': 'PENDING_NATIVE', 'prefix': str(parent / name / ('resources' if name == 'pure_resources' else 'prefix')),
                              'recipe': {'entry': local(directory + '/builder.py'), 'inputs_lock': local(directory + '/inputs.lock.json'),
                                         'source_locks': [local(p) for p in locks]}}
    return {'schema_version': 1, 'kind': 'editor-dependency-prefix-contract',
            'canonical_helper': local('build_files/cmake/platform/platform_ohos_volume.cmake'), 'dependencies': dependencies}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True); p.add_argument('--prefix-parent', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(); repo = absolute(args.repo); parent = absolute(args.prefix_parent, exists=False)
    output = absolute(args.output, exists=False)
    if output.exists() or output.is_relative_to(repo):
        raise ValueError('New external output must be absent and outside repository')
    dump(output, pending(repo, parent))
    print('PREPARED_NOT_RUN: all nine producer declarations remain PENDING_NATIVE')


if __name__ == '__main__':
    main()
