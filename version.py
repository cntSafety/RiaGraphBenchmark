import os

base_dir = os.path.dirname(os.path.realpath(__file__))

# The engine's CMake project is `RiaG` since the rename; `Lbug` is the name before it.
_PROJECT_PREFIXES = ('project(RiaG VERSION', 'project(Lbug VERSION')


def _get_riag_version():
    cmake_file = os.path.join(base_dir, '..', 'CMakeLists.txt')
    with open(cmake_file) as f:
        for line in f:
            if line.startswith(_PROJECT_PREFIXES):
                return line.split(' ')[2].strip()
    return "0"


# Name before the rename, kept for callers outside this repository.
_get_lbug_version = _get_riag_version

if __name__ == "__main__":
    print(_get_riag_version())
