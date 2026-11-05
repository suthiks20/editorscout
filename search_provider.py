import requests

from config import Config


class SearchAPIError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"Search provider returned HTTP {status_code}.")


class SearchTimeoutError(Exception):
    pass


class SearchConnectionError(Exception):
    pass


class SearchResponseError(Exception):
    pass


def search_web(query, num=10, page=1):
    """Return organic results as dictionaries containing title, link, and snippet."""
    if not Config.SERPER_API_KEY:
        raise RuntimeError("Add SERPER_API_KEY to your .env file to search.")

    try:
        response = requests.post(
            Config.SERPER_URL,
            headers={
                "X-API-KEY": Config.SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": num, "page": page, "gl": "in"},
            timeout=Config.SERPER_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise SearchTimeoutError("Serper search timed out.") from exc
    except requests.ConnectionError as exc:
        raise SearchConnectionError("Could not connect to Serper.") from exc
    except requests.RequestException as exc:
        raise SearchConnectionError("Could not connect to Serper.") from exc

    if not response.ok:
        raise SearchAPIError(response.status_code)

    try:
        payload = response.json()
    except (ValueError, requests.JSONDecodeError) as exc:
        raise SearchResponseError("Serper returned an unreadable response.") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("organic"), list):
        raise SearchResponseError("Serper returned an unreadable response.")

    results = []
    for item in payload["organic"]:
        if not isinstance(item, dict):
            raise SearchResponseError("Serper returned an unreadable response.")
        results.append(
            {
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
        )
    return results