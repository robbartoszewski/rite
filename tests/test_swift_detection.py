"""Swift and Xcode projects are detected, and their commands run in a sandbox.

Measured before this existed, on three layouts — a Swift package as a module, a
package at the project root, and an app directory holding an `.xcodeproj` —
detection returned no language, no kind, no frameworks and no commands for
every one. A Worker's CLAUDE.md said "Commands: not detected", and the command
that works inside a seatbelt sandbox is not one anyone guesses: every obvious
one fails with an error pointing somewhere else. `detect._swift_commands`
records why.
"""

import json
import subprocess
from pathlib import Path

import pytest

from rite_ai.cli.init import detect
from rite_ai.cli.init.detect import (
    detect_frameworks,
    detect_kind,
    detect_languages,
    detect_module_commands,
    nests_sandboxes,
    run_detection,
)
from rite_ai.config.models import SandboxConfig

_REAL_SIMULATOR_NAME = detect._ios_simulator_name

_TCA_PACKAGE = """\
// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "NewsTCA",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "NewsTCA", targets: ["NewsTCA"])],
    dependencies: [
        .package(
            url: "https://github.com/pointfreeco/swift-composable-architecture",
            from: "1.15.0"
        ),
    ],
    targets: [.target(name: "NewsTCA")]
)
"""

_IOS_ONLY_PACKAGE = """\
// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "FeedKit",
    platforms: [.iOS(.v17)],
    products: [.library(name: "FeedKit", targets: ["FeedKit"])],
    targets: [.target(name: "FeedKit")]
)
"""

# The two lines detection reads, in the shape Xcode writes them.
_APP_PBXPROJ = """\
// !$*UTF8*$!
{
\tobjects = {
\t\tA1 /* Debug */ = {
\t\t\tisa = XCBuildConfiguration;
\t\t\tbuildSettings = {
\t\t\t\tSDKROOT = iphoneos;
\t\t\t};
\t\t};
\t\tB2 /* XCRemoteSwiftPackageReference */ = {
\t\t\tisa = XCRemoteSwiftPackageReference;
\t\t\trepositoryURL = "https://github.com/pointfreeco/swift-composable-architecture";
\t\t};
\t};
}
"""

# The invocation measured to pass inside a seatbelt sandbox, with TCA, on an
# iOS simulator. Any change to what detection emits has to explain itself here.
_MEASURED_TEST = (
    "xcodebuild test -project NewsApp.xcodeproj -scheme NewsApp "
    "-destination 'platform=iOS Simulator,name=iPhone 17 Pro' "
    "-skipMacroValidation "
    "-IDEPackageSupportDisableManifestSandbox=YES "
    "-IDEPackageSupportDisablePluginExecutionSandbox=YES "
    "'OTHER_SWIFT_FLAGS=$(inherited) -disable-sandbox'"
)
_NESTING_SWITCHES = (
    "-IDEPackageSupportDisableManifestSandbox=YES",
    "-IDEPackageSupportDisablePluginExecutionSandbox=YES",
    "'OTHER_SWIFT_FLAGS=$(inherited) -disable-sandbox'",
)


@pytest.fixture(autouse=True)
def _one_simulator(monkeypatch):
    # What simulators this machine has is not the property under test.
    monkeypatch.setattr(detect, "_ios_simulator_name", lambda: "iPhone 17 Pro")


def _package(dir_path: Path, text: str = _TCA_PACKAGE) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "Package.swift").write_text(text)
    return dir_path


def _app(dir_path: Path, name: str = "NewsApp", schemes: tuple[str, ...] = ()) -> Path:
    project = dir_path / f"{name}.xcodeproj"
    project.mkdir(parents=True)
    (project / "project.pbxproj").write_text(_APP_PBXPROJ)
    # Every .xcodeproj carries a workspace inside it. It is not a workspace
    # the project builds from, and must not be taken for one.
    (project / "project.xcworkspace").mkdir()
    for scheme in schemes:
        shared = project / "xcshareddata" / "xcschemes"
        shared.mkdir(parents=True, exist_ok=True)
        (shared / f"{scheme}.xcscheme").write_text("<Scheme/>\n")
    (dir_path / name).mkdir(exist_ok=True)
    (dir_path / name / "ContentView.swift").write_text("import SwiftUI\n")
    return dir_path


def _git(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        cwd=path,
        check=True,
    )


# --- languages ----------------------------------------------------------------


def test_a_package_manifest_is_swift(tmp_path: Path):
    assert detect_languages(_package(tmp_path)) == ["swift"]


def test_an_xcode_project_with_no_package_manifest_is_swift(tmp_path: Path):
    assert detect_languages(_app(tmp_path)) == ["swift"]


def test_a_workspace_is_swift(tmp_path: Path):
    (tmp_path / "News.xcworkspace").mkdir()
    assert detect_languages(tmp_path) == ["swift"]


def test_loose_swift_sources_are_swift(tmp_path: Path):
    (tmp_path / "main.swift").write_text('print("hi")\n')
    assert detect_languages(tmp_path) == ["swift"]


# --- the three layouts that detected nothing before ---------------------------


def test_a_package_registered_as_a_module(tmp_path: Path):
    _package(tmp_path / "NewsKit")
    _git(tmp_path / "NewsKit")
    summary = run_detection(tmp_path)
    assert [r.name for r in summary.repos] == ["NewsKit"]
    assert summary.languages == ["swift"]
    assert summary.kind == "library"
    assert summary.frameworks == ["composable-architecture"]


def test_a_package_at_the_project_root(tmp_path: Path):
    """Nothing was detected here at all: not a repo subdirectory, no marker."""
    _package(tmp_path)
    summary = run_detection(tmp_path)
    assert summary.has_language_markers
    assert summary.languages == ["swift"]
    assert summary.kind == "library"


def test_an_app_directory_with_an_xcode_project(tmp_path: Path):
    _app(tmp_path / "NewsApp")
    _git(tmp_path / "NewsApp")
    summary = run_detection(tmp_path)
    assert summary.languages == ["swift"]
    assert summary.kind == "mobile"
    assert summary.frameworks == ["composable-architecture"]


# --- kind and frameworks ------------------------------------------------------


def test_an_ios_app_beside_its_feature_package_is_mobile(tmp_path: Path):
    _app(tmp_path)
    _package(tmp_path)
    assert detect_kind(tmp_path) == "mobile"


def test_a_swift_web_framework_is_backend(tmp_path: Path):
    _package(
        tmp_path,
        'let package = Package(name: "api", dependencies: [\n'
        '  .package(url: "https://github.com/vapor/vapor.git", from: "4.0.0"),\n'
        "])\n",
    )
    assert detect_frameworks(tmp_path) == ["vapor"]
    assert detect_kind(tmp_path) == "backend"


def test_an_unrecognised_dependency_is_not_offered_as_a_framework(tmp_path: Path):
    _package(
        tmp_path,
        'let package = Package(name: "x", dependencies: [\n'
        '  .package(url: "https://github.com/apple/swift-log", from: "1.0.0"),\n'
        "])\n",
    )
    assert detect_frameworks(tmp_path) == []


# --- commands: SwiftPM --------------------------------------------------------


def test_a_package_builds_with_swiftpm_outside_a_sandbox(tmp_path: Path):
    cmds = detect_module_commands(_package(tmp_path))
    assert cmds.detected and cmds.source == "Package.swift"
    assert cmds.install == "swift package resolve"
    assert cmds.build == "swift build"
    assert cmds.test == "swift test"


def test_inside_seatbelt_swiftpm_runs_without_its_own_sandbox(tmp_path: Path):
    cmds = detect_module_commands(_package(tmp_path), nested_sandbox=True)
    assert cmds.install == "swift package --disable-sandbox resolve"
    assert cmds.build == "swift build --disable-sandbox"
    assert cmds.test == "swift test --disable-sandbox"


# --- commands: xcodebuild -----------------------------------------------------


def test_detection_emits_the_invocation_measured_to_pass_in_seatbelt(tmp_path: Path):
    cmds = detect_module_commands(_app(tmp_path), nested_sandbox=True)
    assert cmds.test == _MEASURED_TEST
    assert cmds.source == "NewsApp.xcodeproj"


def test_the_nesting_switches_are_left_off_outside_a_sandbox(tmp_path: Path):
    """They remove SwiftPM's and Xcode's own sandboxes. Outside seatbelt nothing
    forbids those, so an unsandboxed build keeps them."""
    cmds = detect_module_commands(_app(tmp_path))
    for command in (cmds.build, cmds.test):
        assert not any(switch in command for switch in _NESTING_SWITCHES)


def test_macro_validation_is_skipped_with_or_without_a_sandbox(tmp_path: Path):
    """Measured on the host with no sandbox: TCA's `xcodebuild test` exits 65
    until someone trusts its macros in Xcode, which no Worker can do."""
    for nested in (False, True):
        cmds = detect_module_commands(_app(tmp_path / str(nested)), nested)
        assert "-skipMacroValidation" in cmds.build
        assert "-skipMacroValidation" in cmds.test


def test_build_needs_no_particular_device(tmp_path: Path):
    cmds = detect_module_commands(_app(tmp_path))
    assert "-destination 'generic/platform=iOS Simulator'" in cmds.build
    assert cmds.install is None


def test_with_no_simulator_the_test_command_is_not_invented(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "_ios_simulator_name", lambda: None)
    cmds = detect_module_commands(_app(tmp_path))
    assert cmds.test is None
    assert cmds.build is not None


def test_the_one_shared_scheme_is_used(tmp_path: Path):
    cmds = detect_module_commands(_app(tmp_path, schemes=("News (Staging)",)))
    assert "-scheme 'News (Staging)'" in cmds.test


def test_several_shared_schemes_fall_back_to_the_project_name(tmp_path: Path):
    cmds = detect_module_commands(_app(tmp_path, schemes=("A", "B")))
    assert "-scheme NewsApp" in cmds.test


def test_a_workspace_is_built_from_rather_than_its_project(tmp_path: Path):
    _app(tmp_path)
    (tmp_path / "NewsApp.xcworkspace").mkdir()
    cmds = detect_module_commands(tmp_path)
    assert cmds.test.startswith("xcodebuild test -workspace NewsApp.xcworkspace ")


def test_an_ios_only_package_is_tested_on_a_simulator(tmp_path: Path):
    cmds = detect_module_commands(_package(tmp_path, _IOS_ONLY_PACKAGE))
    assert cmds.test.startswith("xcodebuild test -scheme FeedKit -destination ")
    assert "-project" not in cmds.test


# --- when the switches apply --------------------------------------------------


@pytest.mark.parametrize(
    ("enabled", "backend", "expected"),
    [
        (True, "seatbelt", True),
        (False, "seatbelt", False),
        (True, "docker", False),
        (True, "tart", False),
    ],
)
def test_only_an_enabled_seatbelt_sandbox_forbids_nesting(enabled, backend, expected):
    assert nests_sandboxes(SandboxConfig(enabled=enabled, backend=backend)) is expected


# --- choosing a simulator -----------------------------------------------------


def _simctl(monkeypatch, payload=None, raises=None):
    def fake_run(*args, **kwargs):
        if raises:
            raise raises
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    monkeypatch.setattr(detect.subprocess, "run", fake_run)


def test_the_newest_ios_runtime_supplies_the_device(monkeypatch):
    runtime = "com.apple.CoreSimulator.SimRuntime."
    _simctl(
        monkeypatch,
        {
            "devices": {
                runtime + "tvOS-26-5": [{"name": "Apple TV", "isAvailable": True}],
                runtime + "iOS-26-2": [{"name": "iPhone 16e", "isAvailable": True}],
                runtime + "iOS-26-5": [
                    {"name": "iPad Air", "isAvailable": True},
                    {"name": "iPhone 17", "isAvailable": True},
                ],
                runtime + "iOS-26-4": [{"name": "iPhone Air", "isAvailable": True}],
            }
        },
    )
    assert _REAL_SIMULATOR_NAME() == "iPhone 17"


def test_no_xcode_tools_means_no_simulator(monkeypatch):
    _simctl(monkeypatch, raises=FileNotFoundError("xcrun"))
    assert _REAL_SIMULATOR_NAME() is None
