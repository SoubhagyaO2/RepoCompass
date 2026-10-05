import pytest

from app.ingestion.url import InvalidRepoURLError, parse_github_url


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/pallets/flask",
        "https://github.com/pallets/flask/",
        "https://github.com/pallets/flask.git",
        "http://github.com/pallets/flask",
        "https://www.github.com/pallets/flask",
        "https://GitHub.com/pallets/flask?tab=readme-ov-file#install",
        "  https://github.com/pallets/flask  ",
    ],
)
def test_valid_urls(url: str) -> None:
    ref = parse_github_url(url)
    assert ref.owner == "pallets"
    assert ref.name == "flask"
    assert ref.clone_url == "https://github.com/pallets/flask.git"


def test_repo_names_with_dots_and_underscores() -> None:
    ref = parse_github_url("https://github.com/some-org/my_repo.js")
    assert ref.full_name == "some-org/my_repo.js"


@pytest.mark.parametrize(
    "url",
    [
        "github.com/pallets/flask",  # no scheme
        "git@github.com:pallets/flask.git",  # SSH form not supported
        "ftp://github.com/pallets/flask",
        "https://gitlab.com/pallets/flask",
        "https://github.com.evil.com/pallets/flask",
        "https://github.com/pallets",  # owner only
        "https://github.com/pallets/flask/tree/main/src",  # sub-path
        "https://github.com/-pallets/flask",  # owner can't start with '-'
        "https://github.com/pallets/..",
        "https://github.com/pallets/fl ask",
        "",
    ],
)
def test_invalid_urls(url: str) -> None:
    with pytest.raises(InvalidRepoURLError):
        parse_github_url(url)
