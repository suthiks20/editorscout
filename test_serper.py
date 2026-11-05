from search_provider import search_web

results = search_web('site:linkedin.com/in "reel editor" Coimbatore')
print("Results found:", len(results))
for r in results[:3]:
    print(r["title"], "-", r["link"])