import time

import httpx

USER_AGENT = "cardledger-database/0.1"
RETRY_DELAYS = (2, 5)  # seconds before the 2nd and the 3rd attempt
sleep = time.sleep  # replaced in tests


def make_client(transport=None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True, transport=transport
    )


def get(
    client: httpx.Client, url: str, timeout: float | None = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    """GET, tried up to 3 times on transient failures (5xx answers, timeouts, connection errors). Returns the last
    answer (the caller checks its status) or raises the last transport error. `timeout` overrides the client's;
    `headers` are added to the client's."""
    kwargs = {} if timeout is None else {"timeout": timeout}
    if headers:
        kwargs["headers"] = headers
    for delay in RETRY_DELAYS:
        try:
            response = client.get(url, **kwargs)
            if response.status_code < 500:
                return response
        except httpx.TransportError:
            pass
        sleep(delay)
    return client.get(url, **kwargs)
