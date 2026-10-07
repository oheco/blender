"""Load precisely inventoried CPython/NumPy extensions from HAP native libs.
Application integration passes its actual trusted native directory after HAP
installation. Pure resources never contain .so files. No environment discovery.
"""
from importlib.abc import MetaPathFinder
from importlib.machinery import ExtensionFileLoader
from importlib.util import spec_from_file_location
from pathlib import Path
import sys


class TrustedNativeFinder(MetaPathFinder):
    def __init__(self, native_directory, modules):
        self.directory = Path(native_directory).resolve(strict=True)
        self.modules = dict(modules)
        for name, basename in self.modules.items():
            if Path(basename).name != basename or not basename.endswith('.so'):
                raise ValueError('Invalid native module basename: ' + name)
            file = self.directory / basename
            if file.is_symlink() or not file.is_file():
                raise ValueError('Missing trusted native module: ' + name)

    def find_spec(self, fullname, path=None, target=None):
        basename = self.modules.get(fullname)
        if basename is None:
            return None
        file = self.directory / basename
        return spec_from_file_location(fullname, file, loader=ExtensionFileLoader(fullname, str(file)))


def install(native_directory, modules):
    finder = TrustedNativeFinder(native_directory, modules)
    sys.meta_path.insert(0, finder)
    return finder
