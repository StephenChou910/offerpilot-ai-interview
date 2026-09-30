import sys
from pathlib import Path

import requests


def main() -> None:
    file_path = Path(sys.argv[1])
    base_url = "http://127.0.0.1:8003/api"
    login = requests.post(base_url + "/auth/login", json={"username": "audit_8a51f0b0", "password": "StagePass123!"})
    login.raise_for_status()
    token = login.json()["access_token"]
    with file_path.open("rb") as file:
        response = requests.post(base_url + "/resumes", headers={"Authorization": f"Bearer {token}"}, files={"file": (file_path.name, file)})
    print(response.status_code)
    print(response.text)
    if response.status_code == 200:
        resume_id = response.json().get("data", {}).get("id")
        if resume_id:
            retry = requests.post(base_url + f"/resumes/{resume_id}/reanalyze", headers={"Authorization": f"Bearer {token}"})
            print("reanalyze", retry.status_code, retry.text)


if __name__ == "__main__":
    main()
