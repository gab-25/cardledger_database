import httpx

USER_AGENT = "cardledger-database/0.1"


def make_client(transport=None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True, transport=transport
    )
