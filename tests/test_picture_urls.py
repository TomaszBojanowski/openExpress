"""Which addresses of device pictures are downloaded (#8): Garmin's own hosts over HTTPS;
Amazon S3 only from a bucket allowed by name (none yet). A refusal names the host and the
first segment of the path at most: never the rest of the address, nor its query."""

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
