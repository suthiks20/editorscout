import os
import requests
from dotenv import load_dotenv

load_dotenv()

key = os.getenv("GOOGLE_API_KEY")
cx = os.getenv("GOOGLE_CX")

print("Key loaded:", bool(key))
print("CX loaded:", bool(cx))

r = requests.get(
    "https://www.googleapis.com/customsearch/v1",
    params={"key": key, "cx": cx, "q": "reel editor Coimbatore"},
    timeout=15,
)

print("Status:", r.status_code)
data = r.json()
if "error" in data:
    print("Message:", data["error"].get("message"))
    for e in data["error"].get("errors", []):
        print("Reason:", e.get("reason"))
else:
    print("Results found:", len(data.get("items", [])))