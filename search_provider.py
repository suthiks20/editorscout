import os
import requests
from dotenv import load_dotenv

load_dotenv()

SERPER_URL = "https://google.serper.dev/search"


def google_search(query, num=10, page=1):
    """Return results shaped like Google Custom Search: a list of
    dicts with title, link and snippet."""
    key = os.getenv("SERPER_API_KEY")
    if not key:
        raise RuntimeError("Add SERPER_API_KEY to your .env file.")

    r = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": num, "page": page, "gl": "in"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()

    return [
        {
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "snippet": item.get("snippet", ""),
        }
        for item in data.get("organic", [])
    ]