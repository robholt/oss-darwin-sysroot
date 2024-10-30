import dataclasses
import glob
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from typing import Optional, Union

SDK_VERSION = "14.4"
OFFICIAL_SDK_PATH = f"/Library/Developer/CommandLineTools/SDKs/MacOSX{SDK_VERSION}.sdk"


@dataclasses.dataclass
class OutputGroup:
    sdk_dir: str
    files: Optional[list[str]] = None
    globs: Optional[list[str]] = None
    directory: Optional[str] = None


@dataclasses.dataclass
class DepInfo:
    path: str


@dataclasses.dataclass
class Symlink:
    dir: str
    link: str
    dest: str


@dataclasses.dataclass
class SDKPackage:
    name: str
    output_groups: list[OutputGroup]
    build_func: Optional[Union[Callable[[], None], Callable[[dict[str, DepInfo]], None]]] = None
    dependencies: Optional[list[str]] = None
    symlinks: Optional[list[Symlink]] = None
    alternate_repo: Optional[str] = None


def architecture_pkg():
    run_cmd(["make", "installhdrs"], {"DSTROOT": f"{os.getcwd()}/out"})


def availability_versions_pkg():
    run_cmd(["make", "installhdrs"])


def dyld_pkg():
    file_contents_replace("include/mach-o/dyld.h", ", bridgeos(5.0)", "")
    file_contents_replace("include/mach-o/dyld.h", "__API_UNAVAILABLE(bridgeos) ", "")
    file_contents_replace("include/mach-o/dyld.h", "DYLD_EXCLAVEKIT_UNAVAILABLE ", "")
    file_contents_replace("include/mach-o/dyld_priv.h", ", bridgeos(3.0)", "")


def icu_pkg():
    file_contents_replace("makefile", "xcrun --sdk macosx --find", "echo -n")
    file_contents_replace("makefile", "xcrun --sdk macosx.internal --show-sdk-path", "xcrun --sdk macosx --show-sdk-path")
    run_cmd([
        "make",
        "MAC_OS_X_VERSION_MIN_REQUIRED=14.0.0",
        "ICU_TARGET_VERSION=-mmacosx-version-min=14.0.0",
        "RC_XBS=YES",
        "installhdrs",
    ])


def libc_pkg():
    os.mkdir("out")
    env = {
        "SRCROOT": os.getcwd(),
        "DSTROOT": "out",
        "PUBLIC_HEADERS_FOLDER_PATH": "include",
        "PRIVATE_HEADERS_FOLDER_PATH": "include",
    }
    run_cmd(["bash", "xcodescripts/headers.sh"], env)
    shutil.copy("out/usr/local/include/ar.h", "out/usr/include/ar.h")
    shutil.rmtree("out/usr/local")


def libinfo_pkg():
    file_contents_replace("xcodescripts/install_files.sh", '-o "$INSTALL_OWNER" -g "$INSTALL_GROUP"', "")
    file_contents_replace("xcodescripts/install_files.sh", "ln -h", "ln -n")
    os.mkdir("out")
    run_cmd(["sh", "xcodescripts/install_files.sh"], {"DSTROOT": "out"})


def libmalloc_pkg():
    file_contents_replace("include/malloc/malloc.h", "TARGET_OS_EXCLAVECORE", "0")
    file_contents_replace("include/malloc/malloc.h", "TARGET_OS_EXCLAVEKIT", "0")


def ncurses_pkg():
    original_cwd = os.getcwd()
    os.chdir("ncurses")
    try:
        run_cmd(["make", "DESTDIR=out", "install.includes"])
    finally:
        os.chdir(original_cwd)


def objc4_pkg():
    file_contents_replace("objc.xcodeproj/project.pbxproj", "macosx.internal", "macosx")
    run_cmd(["xcodebuild", "-target", "objc", "installhdrs", "DSTROOT=out"])


def security_pkg():
    real_framework = f"{OFFICIAL_SDK_PATH}/System/Library/Frameworks/Security.framework"
    needed_headers = glob.glob(os.path.join(real_framework, "Versions/A/Headers/*.h"))
    needed_headers = [hdr.rsplit("/", 1)[-1] for hdr in needed_headers]
    os.mkdir("out")
    shutil.copytree(real_framework, "out/Security.framework", symlinks=True)
    headers_dest = "out/Security.framework/Versions/A/Headers"
    shutil.rmtree(headers_dest)
    os.mkdir(headers_dest)

    osx_headers = set(os.listdir("header_symlinks/macOS/Security"))
    common_headers = set(os.listdir("header_symlinks/Security"))

    for hdr in needed_headers:
        if hdr in osx_headers:
            src = os.path.join("header_symlinks/macOS/Security", hdr)
        elif hdr in common_headers:
            src = os.path.join("header_symlinks/Security", hdr)
        else:
            raise Exception(f"Unable to find {hdr} in Security repo")
        shutil.copy(src, os.path.join(headers_dest, hdr), follow_symlinks=True)


def xnu_pkg(deps: dict[str, DepInfo]):
    coreos_makefiles_dep = deps["CoreOSMakefiles"]
    coreos_makefiles_path = os.path.join(coreos_makefiles_dep.path, "out")
    file_contents_replace("libsyscall/Libsyscall.xcconfig", "<DEVELOPER_DIR>", coreos_makefiles_path)

    file_contents_replace("libkern/libkern/Makefile", "EXPORT_MI_GEN_LIST = version.h", "#EXPORT_MI_GEN_LIST = version.h")
    file_contents_replace("libkern/libkern/Makefile", "version.h: ", "#version.h: ")
    file_contents_replace("libkern/libkern/Makefile", "\t@$(LOG_GENERATE) ", "#\t@$(LOG_GENERATE) ")
    file_contents_replace("libkern/libkern/Makefile", "\t$(_v)install ", "#\t$(_v)install ")
    file_contents_replace("libkern/libkern/Makefile", "\t$(_v)$(NEWVERS) ", "#\t$(_v)$(NEWVERS) ")

    file_contents_replace("makedefs/MakeInc.cmd", " ExclaveKit ExclaveCore ", " ")

    avail_dep = deps["AvailabilityVersions"]
    avail_script = os.path.join(avail_dep.path, "dst/usr/local/libexec/availability.pl")
    os.makedirs("sdk/usr/local/libexec")
    shutil.copy(avail_script, "sdk/usr/local/libexec/availability.pl")

    run_cmd([
        "make",
        "PLATFORM=MacOSX",
        f'SDKVERSION={SDK_VERSION}',
        f'HOST_OS_VERSION={SDK_VERSION}',
        "ARCH=arm64",
        "ARCH_CONFIGS=arm64",
        f'SDKROOT_RESOLVED={OFFICIAL_SDK_PATH}',
        f'HOST_SDKROOT_RESOLVED={OFFICIAL_SDK_PATH}',
        "BUILT_PRODUCTS_DIR=.",
        f'DSTROOT="{os.getcwd()}/out"',
        "RC_DARWIN_KERNEL_VERSION=23.1.0",
        "installhdrs"
    ], allow_failure=True)
    run_cmd([
        "make",
        'PLATFORM=MacOSX',
        f'SDKVERSION={SDK_VERSION}',
        f'HOST_OS_VERSION={SDK_VERSION}',
        "ARCH=arm64",
        "ARCH_CONFIGS=arm64",
        f'SDKROOT_RESOLVED={os.getcwd()}/sdk',
        f'HOST_SDKROOT_RESOLVED={os.getcwd()}/sdk',
        "BUILT_PRODUCTS_DIR=.",
        f'DSTROOT="{os.getcwd()}/out"',
        "RC_DARWIN_KERNEL_VERSION=23.1.0",
        "installhdrs"
    ])

    original_cwd = os.getcwd()
    os.chdir("libsyscall")
    try:
        run_cmd([
            "xcodebuild",
            "-arch",
            "arm64",
            "-target",
            "Build",
            "installhdrs",
            f'DSTROOT={os.getcwd()}/out'
        ])
    finally:
        os.chdir(original_cwd)

    shutil.rmtree("out/usr/local")
    shutil.rmtree("libsyscall/out/usr/local")


def coreos_makefiles_pkg():
    run_cmd(["make", "DSTROOT=out", "install"])
    file_contents_replace("out/Makefiles/CoreOS/Xcode/BSD.xcconfig", "SDKROOT = macosx.internal", "SDKROOT = macosx")
    file_contents_replace("out/Makefiles/CoreOS/Xcode/BSD.xcconfig", "ARCHS_STANDARD_32_64_BIT", "ARCHS_STANDARD")


def launchd_pkg(deps: dict[str, DepInfo]):
    coreos_makefiles_dep = deps["CoreOSMakefiles"]
    coreos_makefiles_path = os.path.join(coreos_makefiles_dep.path, "out")
    file_contents_replace("xcconfigs/common.xcconfig", "<DEVELOPER_DIR>", coreos_makefiles_path)
    run_cmd(["xcodebuild", "-arch", "arm64", "-target", "launchd_libs", "installhdrs", f"DSTROOT={os.getcwd()}/out"])


def cctools_pkg():
    file_contents_replace("xcode/macho_dynamic.xcconfig", "SDKROOT=macosx.internal", "SDKROOT=macosx")
    run_cmd(["xcodebuild", "-target", "macho dynamic", "installhdrs", f"DSTROOT={os.getcwd()}/out"])


def libxslt_pkg():
    shutil.copy("Pregenerated Files/include/libxslt/xsltconfig.h", "libxslt/libexslt/exsltconfig.h")


def libm_pkg():
    run_cmd(["xcodebuild", "-target", "InstallHeaders", "installhdrs", f"DSTROOT={os.getcwd()}/out"])


def corefoundation_pkg(deps: dict[str, DepInfo]):
    patches_path = os.path.join(os.path.dirname(__file__), "cf-patches")
    patches = os.listdir(patches_path)
    patches.sort()
    for patch in patches:
        run_cmd(["patch", "-p1", "-i", os.path.join(patches_path, patch)])

    real_framework = f"{OFFICIAL_SDK_PATH}/System/Library/Frameworks/CoreFoundation.framework"
    icu_includes_path = os.path.join(deps["ICU"].path, "build/usr/local/include")
    libdispatch_includes_path = os.path.join(deps["libdispatch"].path, "private")
    dyld_includes_path = os.path.join(deps["dyld"].path, "include")

    original_cwd = os.getcwd()
    os.chdir("CoreFoundation")
    file_contents_replace("PlugIn.subproj/CFBundlePriv.h", "#if (TARGET_OS_MAC", "#if (0")
    file_contents_replace("Base.subproj/DarwinSymbolAliases", "__TMC15SwiftFoundation19_NSCFConstantString", "#__TMC15SwiftFoundation19_NSCFConstantString")

    os.mkdir("build")
    os.chdir("build")
    env = os.environ.copy()
    env["CFLAGS"] = f"-Wno-error=undef-prefix -DINCLUDE_OBJC=1 -I{icu_includes_path} -I{libdispatch_includes_path} -I{dyld_includes_path}"
    try:
        run_cmd(["cmake", "-DBUILD_SHARED_LIBS=ON", "-DCF_ENABLE_LIBDISPATCH=OFF", ".."], env)
        run_cmd(["make", "CoreFoundation_POPULATE_HEADERS"], env)
        os.unlink("CoreFoundation.framework/CoreFoundation")
        shutil.copy(os.path.join(real_framework, "Versions/A/CoreFoundation.tbd"), "CoreFoundation.framework/Versions/A/CoreFoundation.tbd")
        os.chdir("CoreFoundation.framework")
        os.symlink("Versions/Current/CoreFoundation.tbd", "CoreFoundation.tbd")
    finally:
        os.chdir(original_cwd)


PACKAGES = {
    "CoreOSMakefiles": SDKPackage(
        name="CoreOSMakefiles",
        alternate_repo="https://github.com/apple-oss-distributions/CoreOSMakefiles",
        build_func=coreos_makefiles_pkg,
        output_groups=[],
    ),
    "architecture": SDKPackage(
        name="architecture",
        build_func=architecture_pkg,
        output_groups=[OutputGroup(sdk_dir="", globs=["out/**/*"])]
    ),
    "AvailabilityVersions": SDKPackage(
        name="AvailabilityVersions",
        build_func=availability_versions_pkg,
        output_groups=[OutputGroup(sdk_dir="usr/include", globs=["dst/usr/include/**/*"])]
    ),
    "cctools": SDKPackage(
        name="cctools",
        alternate_repo="https://github.com/apple-oss-distributions/cctools",
        build_func=cctools_pkg,
        output_groups=[OutputGroup(sdk_dir="usr/include", globs=["out/usr/include/**/*"])]
    ),
    "CommonCrypto": SDKPackage(
        name="CommonCrypto",
        output_groups=[OutputGroup(sdk_dir="usr/include/CommonCrypto", globs=["include/*.h", "include/*.modulemap"])]
    ),
    "ICU": SDKPackage(
        name="ICU",
        build_func=icu_pkg,
        output_groups=[OutputGroup(sdk_dir="usr/include/unicode", globs=["build/usr/include/unicode/**/*"])]
    ),
    "Libnotify": SDKPackage(
        name="Libnotify",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["notify.h", "notify_keys.h"])]
    ),
    "Libc": SDKPackage(
        name="Libc",
        build_func=libc_pkg,
        output_groups=[
            OutputGroup(sdk_dir="", globs=["out/**/*"]),
            OutputGroup(sdk_dir="usr/include", files=["include/rune.h", "include/utmp.h"])
        ]
    ),
    "Libm": SDKPackage(
        name="Libm",
        alternate_repo="https://github.com/apple-oss-distributions/Libm",
        build_func=libm_pkg,
        output_groups=[OutputGroup(sdk_dir="usr/include", globs=["out/usr/include/**/*"])]
    ),
    "Libinfo": SDKPackage(
        name="Libinfo",
        build_func=libinfo_pkg,
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["out/usr/local/include/aliasdb.h", "out/usr/local/include/bootparams.h"], globs=["out/usr/include/**/*"])]
    ),
    "bzip2": SDKPackage(
        name="bzip2",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["bzip2/bzlib.h"])]
    ),
    "copyfile": SDKPackage(
        name="copyfile",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["copyfile.h", "xattr_flags.h"])]
    ),
    "dtrace": SDKPackage(
        name="dtrace",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["lib/libdtrace/common/dtrace.h"])]
    ),
    "dyld": SDKPackage(
        name="dyld",
        build_func=dyld_pkg,
        output_groups=[
            OutputGroup(sdk_dir="usr/include", files=["include/dlfcn.h"]),
            OutputGroup(sdk_dir="usr/include/mach-o", files=["include/mach-o/dyld.h", "include/mach-o/dyld_images.h", "include/mach-o/fixup-chains.h", "include/mach-o/utils.h"]),
        ]
    ),
    "expat": SDKPackage(
        name="expat",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["expat/lib/expat.h", "expat/lib/expat_external.h"])]
    ),
    "hfs": SDKPackage(
        name="hfs",
        output_groups=[OutputGroup(sdk_dir="usr/include/hfs", files=["core/hfs_format.h", "core/hfs_unistr.h"])]
    ),
    "launchd": SDKPackage(
        name="launchd",
        alternate_repo="https://github.com/apple-oss-distributions/launchd",
        build_func=launchd_pkg,
        dependencies=["CoreOSMakefiles"],
        output_groups=[OutputGroup(sdk_dir="usr/include", globs=["out/usr/include/*.h"])],
    ),
    "libclosure": SDKPackage(
        name="libclosure",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["Block.h"])]
    ),
    "libdispatch": SDKPackage(
        name="libdispatch",
        output_groups=[
            OutputGroup(sdk_dir="usr/include/dispatch", files=[
                "dispatch/base.h",
                "dispatch/block.h",
                "dispatch/data.h",
                "dispatch/dispatch.h",
                "dispatch/dispatch_swift_shims.h",
                "dispatch/group.h",
                "dispatch/introspection.h",
                "dispatch/io.h",
                "dispatch/object.h",
                "dispatch/once.h",
                "dispatch/queue.h",
                "dispatch/semaphore.h",
                "dispatch/source.h",
                "dispatch/time.h",
                "dispatch/workloop.h",
            ]),
            OutputGroup(sdk_dir="usr/include/os", files=[
                "os/clock.h",
                "os/object.h",
                "os/workgroup.h",
                "os/workgroup_base.h",
                "os/workgroup_interval.h",
                "os/workgroup_object.h",
                "os/workgroup_parallel.h",
            ]),
        ]
    ),
    "libedit": SDKPackage(
        name="libedit",
        output_groups=[
            OutputGroup(sdk_dir="usr/include/editline", files=["src/editline/readline.h"]),
            OutputGroup(sdk_dir="usr/include", files=["src/histedit.h"]),
        ],
        symlinks=[
            Symlink(dir="usr/include/readline", link="history.h", dest="../editline/readline.h"),
            Symlink(dir="usr/include/readline", link="readline.h", dest="../editline/readline.h"),
        ],
    ),
    "libffi": SDKPackage(
        name="libffi",
        output_groups=[
            OutputGroup(sdk_dir="usr/include/ffi", files=["darwin/include/ffi.h", "darwin/include/tramp.h"], globs=["darwin/include/ffitarget*.h"])
        ]
    ),
    "libiconv": SDKPackage(
        name="libiconv",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["citrus/iconv.h", "libcharset/libcharset.h", "libcharset/localcharset.h"])]
    ),
    "libmalloc": SDKPackage(
        name="libmalloc",
        build_func=libmalloc_pkg,
        output_groups=[OutputGroup(sdk_dir="usr/include/malloc", globs=["include/malloc/**/*"])]
    ),
    "libpcap": SDKPackage(
        name="libpcap",
        output_groups=[
            OutputGroup(sdk_dir="usr/include", files=["libpcap/pcap.h", "libpcap/pcap-bpf.h", "libpcap/pcap-namedb.h"]),
            OutputGroup(sdk_dir="usr/include/pcap", globs=["libpcap/pcap/*.h"]),
        ]
    ),
    "libplatform": SDKPackage(
        name="libplatform",
        output_groups=[
            OutputGroup(sdk_dir="usr/include", globs=["include/*.h"]),
            OutputGroup(sdk_dir="usr/include/os", globs=["include/os/*.h"]),
            OutputGroup(sdk_dir="usr/include/libkern", globs=["include/libkern/*.h"])
        ]
    ),
    "libpthread": SDKPackage(
        name="libpthread",
        output_groups=[OutputGroup(sdk_dir="usr/include", globs=["include/**/*"])],
        symlinks=[
            Symlink(dir="usr/include", link="pthread.h", dest="pthread/pthread.h"),
            Symlink(dir="usr/include", link="pthread_impl.h", dest="pthread/pthread_impl.h"),
            Symlink(dir="usr/include", link="pthread_spis.h", dest="pthread/pthread_spis.h"),
            Symlink(dir="usr/include", link="sched.h", dest="pthread/sched.h"),
        ],
    ),
    "libresolv": SDKPackage(
        name="libresolv",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["dns.h", "dns_util.h", "nameser.h", "resolv.h"])],
        symlinks=[Symlink(dir="usr/include/arpa", link="nameser.h", dest="../nameser.h")]
    ),
    "libxml2": SDKPackage(
        name="libxml2",
        output_groups=[OutputGroup(sdk_dir="usr/include/libxml", files=["Pregenerated Files/include/libxml/xmlversion.h"], globs=["libxml2/include/libxml/*.h"])],
        symlinks=[Symlink(dir="usr/include/libxml2", link="libxml", dest="../libxml")]
    ),
    "libxslt": SDKPackage(
        name="libxslt",
        build_func=libxslt_pkg,
        output_groups=[OutputGroup(sdk_dir="usr/include/libexslt", globs=["libxslt/libexslt/*.h"])]
    ),
    "mDNSResponder": SDKPackage(
        name="mDNSResponder",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["mDNSShared/dns_sd.h"])]
    ),
    "ncurses": SDKPackage(
        name="ncurses",
        build_func=ncurses_pkg,
        output_groups=[OutputGroup(
            sdk_dir="usr/include",
            files=["ncurses/menu/eti.h", "ncurses/menu/menu.h", "ncurses/form/form.h", "ncurses/panel/panel.h"],
            globs=["ncurses/include/out/usr/local/include/ncursesw/*"]
        )]
    ),
    "objc4": SDKPackage(
        name="objc4",
        build_func=objc4_pkg,
        output_groups=[OutputGroup(sdk_dir="usr/include/objc", globs=["out/usr/include/objc/*"])]
    ),
    "passwordserver_sasl": SDKPackage(
        name="passwordserver_sasl",
        output_groups=[OutputGroup(sdk_dir="usr/include/sasl", globs=["cyrus_sasl/include/*.h"])]
    ),
    "removefile": SDKPackage(
        name="removefile",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["checkint.h", "removefile.h"])]
    ),
    "Security": SDKPackage(
        name="Security",
        build_func=security_pkg,
        output_groups=[OutputGroup(sdk_dir="System/Library/Frameworks", directory="out/Security.framework")]
    ),
    "syslog": SDKPackage(
        name="syslog",
        output_groups=[OutputGroup(sdk_dir="usr/include", files=["libsystem_asl.tproj/include/asl.h"])]
    ),
    "tidy": SDKPackage(
        name="tidy",
        output_groups=[OutputGroup(sdk_dir="usr/include/tidy", globs=["tidy/include/*"])]
    ),
    "xnu": SDKPackage(
        name="xnu",
        build_func=xnu_pkg,
        dependencies=["AvailabilityVersions", "CoreOSMakefiles"],
        output_groups=[
            OutputGroup(sdk_dir="", globs=["out/**/*", "libsyscall/out/**/*"]),
            OutputGroup(sdk_dir="usr/include", files=["EXTERNAL_HEADERS/AssertMacros.h"]),
        ]
    ),
    "CoreFoundation": SDKPackage(
        name="CoreFoundation",
        alternate_repo="https://github.com/apple/swift-corelibs-foundation",
        build_func=corefoundation_pkg,
        dependencies=["dyld", "ICU", "libdispatch"],
        output_groups=[OutputGroup(sdk_dir="System/Library/Frameworks", directory="CoreFoundation/build/CoreFoundation.framework")]
    )
}


def run_cmd(cmd: list[str], env: Optional[dict[str, str]] = None, allow_failure: bool = False):
    r = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if not allow_failure:
        if r.returncode != 0:
            raise Exception(f"Failed to run '{cmd}'. Exit code {r.returncode}\n{r.stdout.decode()}")


def file_contents_replace(file_path: str, find: str, replace: str):
    modified = False
    with open(file_path, "rb") as f:
        contents = f.readlines()
    for i, line in enumerate(contents):
        line_str = line.decode()
        if line_str.find(find) != -1:
            new_line = line_str.replace(find, replace)
            contents[i] = new_line.encode()
            modified = True
    if modified:
        with open(file_path, "wb") as f:
            f.writelines(contents)


def finalize_sdk():
    run_cmd(["curl", "-LO", "https://github.com/ziglang/zig/raw/0.13.0/lib/libc/include/any-macos-any/TargetConditionals.h"])
    os.rename("TargetConditionals.h", "usr/include/TargetConditionals.h")

    os.makedirs("usr/lib", exist_ok=True)
    shutil.copytree(
        f"{OFFICIAL_SDK_PATH}/usr/lib",
        "usr/lib",
        symlinks=True,
        ignore_dangling_symlinks=True,
        dirs_exist_ok=True,
        ignore=lambda src, names: [n for n in names if not n.endswith(".tbd") and not os.path.isdir(os.path.join(src, n))]
    )

    shutil.rmtree("usr/lib/swift")
    os.chdir("usr/lib")
    for lib in os.listdir("."):
        if os.path.islink(lib):
            if not os.path.exists(os.readlink(lib)):
                os.unlink(lib)


def main():
    os.makedirs("sdk-build", exist_ok=True)
    os.chdir("sdk-build")
    build_root = os.getcwd()

    build_sdk_path = os.path.join(build_root, f"oss-sdk{SDK_VERSION}")
    os.makedirs(build_sdk_path, exist_ok=True)

    built_packages_path = os.path.join(build_root, "built-packages.json")
    built_packages = set()
    if os.path.exists(built_packages_path):
        with open(built_packages_path) as f:
            built_packages = set(json.load(f))
        print(f"previously built packages: {built_packages}")

    if not os.path.exists("./distribution-macOS"):
        print("cloning distribution-macOS")
        run_cmd(["git", "clone", "https://github.com/apple-oss-distributions/distribution-macOS"])
        os.chdir("distribution-macOS")
        repo_version = SDK_VERSION.replace(".", "")
        print("updating distribution-macOS submodules")
        run_cmd(["git", "checkout", f"macos-{repo_version}"])
        run_cmd(["git", "submodule", "update", "--init", "--depth", "1"])

    for pkg_name, pkg in PACKAGES.items():
        if pkg_name in built_packages:
            continue
        print(f"processing {pkg_name}")
        os.chdir(build_root)
        if pkg.alternate_repo is not None:
            os.chdir(build_root)
            repo_name = pkg.alternate_repo.split("/")[-1]
            if not os.path.exists(repo_name):
                run_cmd(["git", "clone", pkg.alternate_repo])
            os.chdir(repo_name)
        else:
            os.chdir(os.path.join("distribution-macOS", pkg.name))

        run_cmd(["git", "reset", "--hard", "HEAD"])
        run_cmd(["git", "clean", "-x", "-f", "-d"])

        if pkg.build_func is not None:
            print(f"building {pkg_name}")
            if pkg.dependencies:
                if not all([dep in built_packages for dep in pkg.dependencies]):
                    raise Exception(f"deps for {pkg_name} not satisfied, a package can only depend on packages that come before it")
                dep_info = {}
                for dep in pkg.dependencies:
                    if PACKAGES[dep].alternate_repo:
                        dep_path_root = build_root
                    else:
                        dep_path_root = os.path.join(build_root, "distribution-macOS")
                    dep_info[dep] = DepInfo(path=os.path.join(dep_path_root, PACKAGES[dep].name))
                pkg.build_func(dep_info)
            else:
                pkg.build_func()

        for out in pkg.output_groups:
            dest = os.path.join(build_sdk_path, out.sdk_dir)
            os.makedirs(dest, exist_ok=True)
            if out.directory:
                dir_name = out.directory.split("/")[-1]
                dir_dest = os.path.join(dest, dir_name)
                os.mkdir(dir_dest)
                shutil.copytree(out.directory, dir_dest, symlinks=True, dirs_exist_ok=True)
            to_copy = []
            if out.files:
                for f in out.files:
                    to_copy.append((f, ""))
            if out.globs:
                for g in out.globs:
                    g_parts = g.split("/")
                    if len(g_parts) > 2 and g_parts[-2] == "**":
                        pfx = "/".join(g_parts[:-2])
                    else:
                        pfx = "/".join(g_parts[:-1])
                    for f in glob.glob(g, recursive=True):
                        d = f.replace(pfx + "/", "")
                        if "/" in d:
                            d = d.rsplit("/", 1)[0]
                        else:
                            d = ""
                        to_copy.append((f, d))
            for (f, d) in to_copy:
                f_name = f.rsplit("/", 1)[-1]
                f_dest = os.path.join(dest, d, f_name)
                if os.path.isdir(f):
                    os.makedirs(f_dest, exist_ok=True)
                else:
                    shutil.copy(f, f_dest)

        if pkg.symlinks:
            for symlink in pkg.symlinks:
                original_cwd = os.getcwd()
                symlink_dir = os.path.join(build_sdk_path, symlink.dir)
                if not os.path.exists(symlink_dir):
                    os.makedirs(symlink_dir)
                os.chdir(symlink_dir)
                os.symlink(symlink.dest, symlink.link)
                os.chdir(original_cwd)

        built_packages.add(pkg_name)
        with open(built_packages_path, "w") as f:
            json.dump(list(built_packages), f)
        print(f"{pkg_name} complete")

    print("finalizing sdk")
    os.chdir(build_sdk_path)
    finalize_sdk()
    print("sdk complete!")


if __name__ == '__main__':
    main()
