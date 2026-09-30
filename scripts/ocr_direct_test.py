import sys
import requests

with open(sys.argv[1], "rb") as file:
    response = requests.post("http://127.0.0.1:8010/ocr", files={"file": (sys.argv[1], file)}, timeout=180)
print(response.status_code)
print(response.text[:5000])
