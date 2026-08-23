"""Programmatic login to the IdP to obtain a JWT token.

Solves the math challenge automatically and returns a valid JWT
that can be used for git operations and API calls.
"""

from __future__ import annotations

import json
import re
import subprocess

import requests
from bs4 import BeautifulSoup


IDP_BASE_URL = "https://idp.botthouse.net"


def get_test_credentials() -> dict:
    """Get test credentials from secretcli.

    Returns:
        Dict with 'user' and 'password' keys.
    """
    result = subprocess.run(
        ["secretcli", "get", "wikitest"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def login_to_idp(
    service: str = "git",
    callback_url: str = "https://git.botthouse.net/auth/callback",
    username: str | None = None,
    password: str | None = None,
) -> str | None:
    """Login to the IdP programmatically and return the JWT token.

    Handles the math challenge automatically.

    Args:
        service: The IdP service name.
        callback_url: The OAuth callback URL.
        username: Override username (defaults to secretcli wikitest).
        password: Override password.

    Returns:
        The JWT token string, or None if login failed.
    """
    if username is None or password is None:
        creds = get_test_credentials()
        username = username or creds["user"]
        password = password or creds["password"]

    session = requests.Session()

    # GET the login page to get CSRF token, challenge hash, and challenge
    login_url = f"{IDP_BASE_URL}/{service}?callback={callback_url}"
    resp = session.get(login_url, timeout=10)
    if resp.status_code != 200:
        return None

    # Parse the form
    soup = BeautifulSoup(resp.text, "html.parser")

    csrf_token = ""
    csrf_input = soup.find("input", {"name": "csrf_token"})
    if csrf_input:
        csrf_token = csrf_input.get("value", "")

    challenge_hash = ""
    hash_input = soup.find("input", {"name": "challenge_hash"})
    if hash_input:
        challenge_hash = hash_input.get("value", "")

    # Solve the math challenge
    challenge_answer = ""
    challenge_div = soup.find("div", class_="challenge-question")
    if challenge_div:
        question = challenge_div.get_text()
        match = re.search(r"(\d+)\s*([+\-*×÷/])\s*(\d+)", question)
        if match:
            n1 = int(match.group(1))
            op = match.group(2)
            n2 = int(match.group(3))
            if op == "+":
                challenge_answer = str(n1 + n2)
            elif op == "-":
                challenge_answer = str(n1 - n2)
            elif op in ("*", "×"):
                challenge_answer = str(n1 * n2)
            elif op in ("/", "÷"):
                challenge_answer = str(n1 // n2)

    # POST the login form
    data = {
        "username": username,
        "password": password,
        "csrf_token": csrf_token,
        "challenge_hash": challenge_hash,
        "challenge_answer": challenge_answer,
    }

    # Don't follow redirects — we want to capture the token from the
    # redirect URL
    resp = session.post(
        f"{IDP_BASE_URL}/{service}",
        data=data,
        allow_redirects=False,
        timeout=10,
    )

    # Check for redirect with token
    if resp.status_code in (302, 303):
        location = resp.headers.get("Location", "")
        if "token=" in location:
            token = location.split("token=", 1)[1]
            # Remove any trailing params
            if "&" in token:
                token = token.split("&")[0]
            return token

    return None


if __name__ == "__main__":
    token = login_to_idp()
    if token:
        print(f"Token obtained: {token[:50]}...")
    else:
        print("Login failed")
