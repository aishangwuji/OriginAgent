from __future__ import annotations

from OpenHome.integrations.content_read.providers.github import parse_github_url
from OpenHome.integrations.content_read.providers.rss import is_rss_like_url


def test_parse_github_url_supports_repo_and_blob() -> None:
    assert parse_github_url("https://github.com/iBigQiang/feedgrab") == (
        "iBigQiang",
        "feedgrab",
        None,
    )
    assert parse_github_url(
        "https://github.com/iBigQiang/feedgrab/blob/main/README.md"
    ) == ("iBigQiang", "feedgrab", "README.md")


def test_rss_url_detection_is_narrow() -> None:
    assert is_rss_like_url("https://example.com/feed.xml")
    assert is_rss_like_url("https://example.com/rss")
    assert not is_rss_like_url("https://example.com/article")
