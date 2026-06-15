"""Pre-filled GitHub issue-form URLs (#56)."""
from urllib.parse import parse_qs, urlparse

from core.issue_report import (
    build_bug_report_url, build_feature_request_url, detect_platform_option,
)
from core.version import APP_VERSION

_PLATFORM_OPTIONS = {
    "macOS (Apple Silicon)", "macOS (Intel)",
    "Windows (x64)", "Windows (ARM64)", "Linux", "Other / not sure",
}


def test_platform_option_is_a_template_choice():
    assert detect_platform_option() in _PLATFORM_OPTIONS


def test_bug_url_prefills_detected_fields():
    q = parse_qs(urlparse(build_bug_report_url()).query)
    assert q["template"] == ["bug_report.yml"]
    assert q["chromiq_version"] == [f"v{APP_VERSION}"]
    assert q["platform"][0] in _PLATFORM_OPTIONS
    assert q["os_version"][0]                       # non-empty


def test_feature_url_prefills_platform():
    q = parse_qs(urlparse(build_feature_request_url()).query)
    assert q["template"] == ["feature_request.yml"]
    assert q["platform"][0] in _PLATFORM_OPTIONS
