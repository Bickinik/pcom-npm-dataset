import urllib.request
import json
import time

packages = set()
print("Fetching packages from npm...")

for i in range(4):
    url = f"https://registry.npmjs.org/-/v1/search?text=popularity:1&size=250&from={i*250}"
    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read().decode())
            for item in data.get('objects', []):
                packages.add(item['package']['name'])
        time.sleep(1) # Rate limit pacing
    except Exception as e:
        print(f"Error on page {i}: {e}")

with open('extra_packages.txt', 'w') as f:
    for pkg in sorted(packages):
        f.write(f"{pkg}\n")

print(f"Successfully saved {len(packages)} packages to extra_packages.txt")