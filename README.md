# Building a Free Darwin sysroot

The `build.py` script in this repo will assemble a mostly complete Darwin sysroot using only components from open
source projects. The resulting sysroot can be used as part of a cross-compilation toolchain for macOS. Because all
components of the sysroot originate from projects released under an OSS license, or are non-copyrightable generated
data, the generated sysroot can be used on systems where the macOS SDK cannot be used due to license restrictions.
