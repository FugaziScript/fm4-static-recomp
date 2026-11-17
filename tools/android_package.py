"""Packages the Android build (out/build/android-arm64-release) as an APK.

The package holds the game translated from the player's own disc, so it is built on their PC,
installed on their own device and never published. The steps follow Pinyon Shift's
tools/pinyon_android.py (BSD 3-Clause, arcanite24/pinyon-shift): the manifest linked by aapt2,
SDL's Java classes and the activity compiled by javac and d8, the native libraries stripped,
then aligned by zipalign and signed by apksigner with a key made on this PC.

Needs ANDROID_HOME (build-tools 36.0.0, platform android-36), ANDROID_NDK_HOME and a JDK 17 on
PATH. Usage: python tools/android_package.py [--build DIR] [--out APK]
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "fm4.staticrecomp"
MIN_SDK, TARGET_SDK = 33, 35
BUILD_TOOLS, PLATFORM = "36.0.0", "android-36"
# Every library the game loads, besides the C++ runtime.
LIBRARIES = (
    "librexruntime.so",
    "librexgpu-fh1.so",
    "libfm4_static_recomp_SpeechFacade_default.so",
    "libfm4_static_recomp_XMediaFacade_default.so",
    "libmain.so",
)
# Loaded by name when present: FSR 2/3 (ShiftGlue's thirdparty/fidelityfx-vk) and
# libadrenotools' hooks for a custom Vulkan driver.
OPTIONAL_LIBRARIES = (
    "libamd_fidelityfx_vk.so",
    "libhook_impl.so",
    "libmain_hook.so",
    "libfile_redirect_hook.so",
    "libgsl_alloc_hook.so",
    # Lossless Scaling frame generation (lsfg-vk's framegen, MIT); the shaders come from the
    # player's own Lossless.dll on the device.
    "liblsfg_framegen.so",
)
EXE = ".exe" if os.name == "nt" else ""
BAT = ".bat" if os.name == "nt" else ""


def run(*command):
    print("+", " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True)


def find(directory: Path, name: str) -> Path | None:
    found = [path for path in directory.rglob(name) if "CMakeFiles" not in path.parts]
    return max(found, key=lambda path: path.stat().st_mtime) if found else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, default=ROOT / "out/build/android-arm64-release")
    parser.add_argument("--out", type=Path, default=ROOT / "out/android/fm4-static-recomp.apk")
    parser.add_argument("--version-name", default="0.1-android-test")
    parser.add_argument("--version-code", type=int, default=1)
    args = parser.parse_args()

    sdk = Path(os.environ["ANDROID_HOME"])
    ndk = Path(os.environ["ANDROID_NDK_HOME"])
    tools = sdk / "build-tools" / BUILD_TOOLS
    android_jar = sdk / "platforms" / PLATFORM / "android.jar"
    llvm = next((ndk / "toolchains" / "llvm" / "prebuilt").iterdir()) / "bin"
    rexsdk = Path(os.environ.get("REXSDK_DIR", ROOT.parent / "shiftglue-sdk"))

    staging = args.out.parent / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "classes").mkdir(parents=True)
    (staging / "dex").mkdir()
    (staging / "lib").mkdir()

    manifest = (ROOT / "android" / "AndroidManifest.xml").read_text(encoding="utf-8")
    manifest = manifest.replace("<manifest ", f'<manifest package="{PACKAGE}" ', 1)
    (staging / "AndroidManifest.xml").write_text(manifest, encoding="utf-8")
    unsigned = staging / "unsigned.apk"
    run(tools / f"aapt2{EXE}", "link", "-o", unsigned, "-I", android_jar,
        "--manifest", staging / "AndroidManifest.xml",
        "--min-sdk-version", MIN_SDK, "--target-sdk-version", TARGET_SDK,
        "--version-name", args.version_name, "--version-code", args.version_code,
        # FM4_ANDROID_DEBUGGABLE=1: a debuggable APK, which simpleperf can profile.
        *(["--debug-mode"] if os.environ.get("FM4_ANDROID_DEBUGGABLE") == "1" else []))

    sdl_java = rexsdk / "thirdparty/sdl3/android-project/app/src/main/java"
    sources = sorted(sdl_java.rglob("*.java")) + sorted((ROOT / "android/java").rglob("*.java"))
    run("javac", "-source", "1.8", "-target", "1.8", "-nowarn", "-Xlint:-options",
        "-bootclasspath", android_jar, "-classpath", android_jar, "-encoding", "UTF-8",
        "-d", staging / "classes", *sources)
    classes = staging / "classes.jar"
    with zipfile.ZipFile(classes, "w", zipfile.ZIP_DEFLATED) as jar:
        for path in sorted((staging / "classes").rglob("*.class")):
            jar.write(path, path.relative_to(staging / "classes").as_posix())
    run(tools / f"d8{BAT}", "--release", "--min-api", MIN_SDK, "--lib", android_jar,
        "--output", staging / "dex", classes)

    libraries = []
    for name in LIBRARIES:
        path = find(args.build, name)
        if not path:
            raise SystemExit(f"{name} is not built in {args.build}")
        libraries.append(path)
    for name in OPTIONAL_LIBRARIES:
        path = find(args.build, name)
        if path:
            libraries.append(path)
        else:
            print(f"(optional {name} not built, left out)")
    libraries.append(ndk / "toolchains/llvm/prebuilt" / llvm.parent.name / "sysroot/usr/lib"
                     / "aarch64-linux-android" / "libc++_shared.so")
    stripped = []
    for library in libraries:
        target = staging / "lib" / library.name
        run(llvm / f"llvm-strip{EXE}", "--strip-debug", "-o", target, library)
        stripped.append(target)

    # Mesa Turnip for Adreno (an adrenotools package: meta.json and the driver library), which
    # the activity installs into state/drivers. Stored uncompressed: the activity reads its size
    # through openFd.
    # Every adrenotools package (a folder with meta.json) under the toolchain's drivers folder.
    drivers_root = Path(os.environ.get("FM4_ANDROID_DRIVERS", ROOT.parent / "android-toolchain/drivers"))
    drivers = sorted(path for path in drivers_root.iterdir()
                     if (path / "meta.json").is_file()) if drivers_root.is_dir() else []
    with zipfile.ZipFile(unsigned, "a", compression=zipfile.ZIP_DEFLATED) as apk:
        apk.write(staging / "dex" / "classes.dex", "classes.dex")
        for library in stripped:
            apk.write(library, f"lib/arm64-v8a/{library.name}")
        for driver in drivers:
            for path in sorted(driver.iterdir()):
                apk.write(path, f"assets/drivers/{driver.name}/{path.name}",
                          compress_type=zipfile.ZIP_STORED)
            print(f"driver {driver.name} bundled")
        if not drivers:
            print(f"(no GPU drivers under {drivers_root}, left out)")

    aligned = staging / "aligned.apk"
    run(tools / f"zipalign{EXE}", "-f", "-p", "4", unsigned, aligned)
    keystore = args.out.parent / "fm4-local.keystore"
    if not keystore.exists():
        run("keytool", "-genkeypair", "-keystore", keystore, "-alias", "fm4", "-keyalg", "RSA",
            "-keysize", "2048", "-validity", "10000", "-storepass", "fm4local",
            "-keypass", "fm4local", "-dname", "CN=FM4 Static Recomp local build")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    run(tools / f"apksigner{BAT}", "sign", "--ks", keystore, "--ks-pass", "pass:fm4local",
        "--key-pass", "pass:fm4local", "--out", args.out, aligned)
    print(f"APK: {args.out} ({args.out.stat().st_size / 2**20:.0f} MB)")
    for library in stripped:
        print(f"  {library.name}: {library.stat().st_size / 2**20:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
