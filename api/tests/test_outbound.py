import pytest

from app.outbound import OutboundRefused, check_target


def test_private_and_loopback_targets_are_refused_by_default():
    for url in ("http://127.0.0.1:4000/x", "http://localhost/x", "http://169.254.169.254/latest/meta-data"):
        with pytest.raises(OutboundRefused):
            check_target(url)


def test_an_allowed_host_may_be_private_and_nothing_else_may():
    check_target("http://127.0.0.1:4000/x", allow_hosts=["127.0.0.1"])
    check_target("http://LOCALHOST:4000/x", allow_hosts=["localhost"])  # matched without regard to case
    with pytest.raises(OutboundRefused):
        check_target("http://169.254.169.254/x", allow_hosts=["127.0.0.1", "localhost"])
    with pytest.raises(OutboundRefused):
        check_target("http://localhost:8001/x", allow_hosts=["localhost.evil.test"])  # exact host names only


def test_the_scheme_is_checked_even_for_an_allowed_host():
    with pytest.raises(OutboundRefused):
        check_target("file://localhost/etc/passwd", allow_hosts=["localhost"])
