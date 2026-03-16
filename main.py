import getpass
from typing import Any

import requests
from requests.auth import HTTPBasicAuth


REGISTER_URL = "https://api.watttime.org/register"
LOGIN_URL = "https://api.watttime.org/login"
REGION_URL = "https://api.watttime.org/v3/region-from-loc"


def login(username: str, password: str) -> str:
    response = requests.get(LOGIN_URL, auth=HTTPBasicAuth(username, password))
    response.raise_for_status()
    return response.json()["token"]


def get_region(token: str, latitude: float, longitude: float, signal_type: str = "co2_moer") -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    params: dict[str, str | float] = {
        "latitude": latitude,
        "longitude": longitude,
        "signal_type": signal_type,
    }
    response = requests.get(REGION_URL, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def main():
    print("=== WattTime Region Lookup ===\n")

    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")

    print("\nLogging in...")
    token = login(username, password)
    print("Login successful.")
    print(token)

    # Test coordinates: San Francisco, CA
    # 32.93540301002531, -96.57213525171596
    latitude = 32.93540301002531
    longitude = -96.57213525171596

    print(f"\nLooking up region for coordinates: ({latitude}, {longitude})")
    result = get_region(token, latitude, longitude)
    print("Result:", result)


if __name__ == "__main__":
    main()

