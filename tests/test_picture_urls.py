"""Which addresses of device pictures are downloaded (#8): Garmin's own hosts over HTTPS;
Amazon S3 only from a bucket allowed by name (Garmin Connect's). A refusal names the host and
the first segment of the path at most: never the rest of the address, nor its query."""

import pytest

from openexpress import picture_urls


@pytest.mark.parametrize("url", [
    "https://res.garmin.com/en/products/010-02906-11/v/c1_01_md.png",
    "https://static.garmincdn.com/pumac/fenix.jpg",
    "https://RES.garmin.com/a.png",  # a host in any letter case
    "https://res.garmin.com:443/a.png",  # the default port, said
])
def test_garmins_own_hosts_are_allowed(url):
    assert picture_urls.refusal(url) is None and picture_urls.allowed(url)


@pytest.mark.parametrize("url, why", [
    ("http://res.garmin.com/a.png", "not HTTPS"),
    ("ftp://res.garmin.com/a.png", "not HTTPS"),
    ("res.garmin.com/a.png", "not HTTPS"),
    ("", "no address"),
    (None, "no address"),
    ("https://name:secret@res.garmin.com/a.png",
     "host res.garmin.com: a user name in the address"),
    ("https://res.garmin.com:8443/a.png", "host res.garmin.com: port 8443"),
    ("https://example.org/a.png", "host example.org is not allowed"),
    ("https://res.garmin.com.example.org/a.png",
     "host res.garmin.com.example.org is not allowed"),
    ("https://s3.amazonaws.com/garmin-bucket/devices/fenix.png?X-Amz-Signature=secret",
     "host s3.amazonaws.com, first segment garmin-bucket: not an allowed bucket"),
    ("https://s3.eu-west-1.amazonaws.com/garmin-bucket/devices/fenix.png",
     "host s3.eu-west-1.amazonaws.com, first segment garmin-bucket: not an allowed bucket"),
    ("https://garmin-pics.s3.amazonaws.com/devices/fenix.png?sig=secret",
     "host garmin-pics.s3.amazonaws.com: not an allowed bucket"),
    ("https://garmin-pics.s3.us-east-1.amazonaws.com/devices/fenix.png",
     "host garmin-pics.s3.us-east-1.amazonaws.com: not an allowed bucket"),
])
def test_anything_else_is_refused_with_its_host_only(url, why):
    assert picture_urls.refusal(url) == why and not picture_urls.allowed(url)
    for private in ("secret", "devices", "fenix", "Signature", "sig="):
        assert private not in why


def test_a_bucket_of_garmin_on_s3_is_allowed_once_named(monkeypatch):
    monkeypatch.setattr(picture_urls, "S3_BUCKETS", frozenset({"garmin-bucket", "garmin-pics"}))
    assert picture_urls.allowed("https://s3.amazonaws.com/garmin-bucket/a.png")
    assert picture_urls.allowed("https://garmin-pics.s3.us-east-1.amazonaws.com/a.png")
    assert picture_urls.refusal("https://s3.amazonaws.com/other/a.png") == (
        "host s3.amazonaws.com, first segment other: not an allowed bucket")
    assert picture_urls.refusal("http://s3.amazonaws.com/garmin-bucket/a.png") == "not HTTPS"


@pytest.mark.parametrize("url", [
    "https://s3.amazonaws.com/garmin-connect-prod/device_images/a.png",
    "https://s3.amazonaws.com/garmin-connect-prod/a.png?X-Amz-Signature=secret",
    "https://s3.us-east-1.amazonaws.com/garmin-connect-prod/a.png",
    "https://s3-external-1.amazonaws.com/garmin-connect-prod/a.png",
    "https://garmin-connect-prod.s3.amazonaws.com/a.png",
    "https://garmin-connect-prod.s3.us-east-1.amazonaws.com/a.png",
    "https://GARMIN-CONNECT-PROD.S3.AMAZONAWS.COM/a.png",  # a host in any letter case
])
def test_the_bucket_of_garmin_connect_on_s3_is_allowed(url):
    """#8, the log of 0.6.10 (22.10.2026): the account gives the pictures of three models in
    the bucket garmin-connect-prod, by the path; by the host it is allowed as well."""
    assert picture_urls.refusal(url) is None and picture_urls.allowed(url)


@pytest.mark.parametrize("url, why", [
    ("https://s3.amazonaws.com/garmin-connect-dev/a.png?X-Amz-Signature=secret",
     "host s3.amazonaws.com, first segment garmin-connect-dev: not an allowed bucket"),
    ("https://s3.amazonaws.com/garmin-connect-prod-copy/a.png",
     "host s3.amazonaws.com, first segment garmin-connect-prod-copy: not an allowed bucket"),
    ("https://s3.amazonaws.com/other/garmin-connect-prod/a.png",
     "host s3.amazonaws.com, first segment other: not an allowed bucket"),
    ("https://s3.amazonaws.com/Garmin-Connect-Prod/a.png",  # a bucket is named in lower case
     "host s3.amazonaws.com, first segment Garmin-Connect-Prod: not an allowed bucket"),
    ("https://other.garmin-connect-prod.s3.amazonaws.com/a.png",
     "host other.garmin-connect-prod.s3.amazonaws.com: not an allowed bucket"),
    ("https://garmin-connect-prod.s3.amazonaws.com.example.org/a.png",
     "host garmin-connect-prod.s3.amazonaws.com.example.org is not allowed"),
    ("https://garmin-connect-prod.example.org/a.png",
     "host garmin-connect-prod.example.org is not allowed"),
    ("https://s3.amazonaws.com:8443/garmin-connect-prod/a.png",
     "host s3.amazonaws.com: port 8443"),
    ("http://s3.amazonaws.com/garmin-connect-prod/a.png", "not HTTPS"),
])
def test_any_other_bucket_is_still_refused_with_its_host_and_bucket_only(url, why):
    assert picture_urls.refusal(url) == why and not picture_urls.allowed(url)
    for private in ("secret", "Signature", "a.png"):
        assert private not in why


@pytest.mark.parametrize("url, asked", [
    ("http://res.garmin.com/a.png", "https://res.garmin.com/a.png"),
    ("HTTP://static.garmincdn.com/a.jpg", "https://static.garmincdn.com/a.jpg"),
    ("https://res.garmin.com/a.png", "https://res.garmin.com/a.png"),
    ("ftp://res.garmin.com/a.png", "ftp://res.garmin.com/a.png"),  # for refusal() to judge
    (None, None),
])
def test_plain_http_is_asked_over_https_before_the_rule(url, asked):
    """As every download of the program (download.secure_url): plain HTTP over HTTPS; the
    rule then still asks for HTTPS and Garmin's hosts."""
    assert picture_urls.upgraded(url) == asked
    assert picture_urls.refusal(picture_urls.upgraded("http://example.org/a.png")) == (
        "host example.org is not allowed")


@pytest.mark.parametrize("url, said", [
    ("https://res.garmin.com/a.png", "no query"),
    ("https://res.garmin.com/a.png?", "no query"),  # an empty query says nothing
    ("https://s3.amazonaws.com/garmin-connect-prod/a.png?X-Amz-Signature=secret",
     "with query"),
    ("ftp://example.org/a.png?token=secret", "with query"),
    ("a.png", "no query"),
    ("", None),
    (None, None),
    ("https://[::1/a.png?x=1", None),  # not an address
])
def test_the_log_says_whether_an_address_has_a_query_never_what(url, said):
    """For the log, next to the host: a signed address carries its token in the query."""
    assert picture_urls.query_said(url) == said
