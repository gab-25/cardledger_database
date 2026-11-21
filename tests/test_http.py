import httpx
import pytest

from cardledger_database import http


def flaky(*answers):
    """A client answering with each of `answers` in turn (a status code or an exception to raise)."""
    calls = []

    def handler(request):
        answer = answers[len(calls)]
        calls.append(request.url.path)
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, json={"ok": True})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


@pytest.mark.parametrize("first", [500, 503, httpx.ConnectError("down"), httpx.ReadTimeout("slow")])
def test_transient_failure_is_retried(first, sleeps):
    client, calls = flaky(first, 200)
    assert http.get(client, "https://x/a").status_code == 200
    assert len(calls) == 2 and sleeps == [http.RETRY_DELAYS[0]]


def test_gives_up_after_three_attempts(sleeps):
    client, calls = flaky(502, 502, 502)
    assert http.get(client, "https://x/a").status_code == 502
    assert len(calls) == 3 and sleeps == list(http.RETRY_DELAYS)

    client, calls = flaky(*[httpx.ConnectError("down")] * 3)
    with pytest.raises(httpx.ConnectError):
        http.get(client, "https://x/a")
    assert len(calls) == 3


@pytest.mark.parametrize("status", [200, 404, 429, 400])
def test_other_answers_are_not_retried(status, sleeps):
    client, calls = flaky(status)
    assert http.get(client, "https://x/a").status_code == status
    assert len(calls) == 1 and sleeps == []
