from search_provider import google_search

results = google_search('site:linkedin.com/in "reel editor" Coimbatore')
print("Results found:", len(results))
for r in results[:3]:
    print(r["title"], "-", r["link"])