# pip install kiteconnect python-dotenv
import json
import os
import threading
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from kite_selenium_login import selenium_login_and_get_request_token
from kiteconnect import KiteConnect

TOKEN_PATH_DEFAULT = "kite_session.json"
REDIRECT_URL_DEFAULT = "http://127.0.0.1:8765/callback"
CALLBACK_PORT = 8765


class _RequestTokenHandler(BaseHTTPRequestHandler):
    """Minimal handler to capture ?request_token=... from Zerodha redirect."""
    request_token_holder = {"token": None}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        params = parse_qs(parsed.query)
        req_token = params.get("request_token", [None])[0]
        if not req_token:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing request_token")
            return

        # Save and ack
        _RequestTokenHandler.request_token_holder["token"] = req_token
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Login successful. You may close this tab.")

        # Stop the server as soon as we’ve captured the token
        def shutdown_server(server):
            try:
                server.shutdown()
            except Exception:
                pass

        threading.Thread(target=shutdown_server, args=(self.server,), daemon=True).start()

    # Silence default logging
    def log_message(self, format, *args):
        return


def _start_callback_server():
    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _RequestTokenHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _save_session(token_path, api_key, access_token):
    payload = {
        "api_key": api_key,
        "access_token": access_token,
        "login_date": date.today().isoformat(),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(token_path, "w") as f:
        json.dump(payload, f, indent=2)


def _load_session(token_path):
    if not os.path.exists(token_path):
        return None
    with open(token_path, "r") as f:
        data = json.load(f)
    if data.get("login_date") != date.today().isoformat():
        return None
    return data


'''
def get_kite_client(
    api_key: str,
    api_secret: str,
    *,
    token_path: str = TOKEN_PATH_DEFAULT,
    redirect_url: str = REDIRECT_URL_DEFAULT,
    open_browser: bool = True,
    timeout_seconds: int = 180,
):
    """
    Returns an authenticated KiteConnect client.
    - Reuses today's access_token from disk if present.
    - Otherwise opens Zerodha login (password + TOTP on Zerodha page),
      captures request_token via a tiny local HTTP server, exchanges it,
      saves access_token, and returns the client.

    Usage:
        from kite_auth import get_kite_client
        kite = get_kite_client(API_KEY, API_SECRET)
    """
    # 1) Try reusing today's token
    print("[..Initiating Authentication Process...]")
    saved = _load_session(token_path)
    if saved:
        kite = KiteConnect(api_key=saved["api_key"])
        kite.set_access_token(saved["access_token"])
        return kite

    # 2) Fresh login
    server = _start_callback_server()
    print("[..Token Expired Obtaining new Token...]")
    try:
        kite = KiteConnect(api_key=api_key)
        # Build login URL (Zerodha-hosted page handles password + TOTP)
        login_url = kite.login_url()

        if open_browser:
            webbrowser.open(login_url)
        else:
            print(f"Open this URL to login: {login_url}")

        # Wait (up to timeout) for the local server to receive request_token
        for _ in range(timeout_seconds):
            token = _RequestTokenHandler.request_token_holder["token"]
            if token:
                # Exchange request_token -> access_token
                data = kite.generate_session(token, api_secret=api_secret)
                access_token = data["access_token"]
                _save_session(token_path, api_key, access_token)

                kite = KiteConnect(api_key=api_key)
                kite.set_access_token(access_token)
                return kite
            # Sleep 1s without importing time at top unnecessarily
            import time as _t
            _t.sleep(1)

        raise TimeoutError(
            "Timed out waiting for request_token. Did you complete the Zerodha login?"
        )
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
'''
def get_kite_client(
    api_key: str,
    api_secret: str,
    *,
    token_path: str = TOKEN_PATH_DEFAULT,
    redirect_url: str = REDIRECT_URL_DEFAULT,
    timeout_seconds: int = 180,
):
    """
    Returns an authenticated KiteConnect client.

    Flow:
    1. Reuse today's saved access_token if available
    2. Else:
        - Open Zerodha login via Selenium
        - Auto-fill user id, password, TOTP
        - Capture request_token
        - Exchange for access_token
        - Save session
    """

    from kiteconnect import KiteConnect
    import os
    import time

    from kite_selenium_login import selenium_login_and_get_request_token

    print("[AUTH] Initializing Kite authentication")

    # ---------------------------------------------------------
    # 1️⃣ Try reusing today's saved session
    # ---------------------------------------------------------
    saved = _load_session(token_path)
    if saved:
        print("[AUTH] Using saved access token")
        kite = KiteConnect(api_key=saved["api_key"])
        kite.set_access_token(saved["access_token"])
        return kite

    # ---------------------------------------------------------
    # 2️⃣ Fresh login using Selenium
    # ---------------------------------------------------------
    print("[AUTH] Saved token missing/expired → starting auto login")

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()

    user_id = os.getenv("KITE_USER_ID")
    password = os.getenv("KITE_PASSWORD")
    totp_secret = os.getenv("KITE_TOTP_SECRET")

    if not user_id or not password or not totp_secret:
        raise RuntimeError(
            "❌ Missing one of KITE_USER_ID / KITE_PASSWORD / KITE_TOTP_SECRET in environment"
        )

    # --- Selenium handles Zerodha login ---
    request_token = selenium_login_and_get_request_token(
        login_url=login_url,
        user_id=user_id,
        password=password,
        totp_secret=totp_secret,
        timeout=timeout_seconds,
    )

    if not request_token:
        raise RuntimeError("❌ Failed to obtain request_token from Zerodha")

    print("[AUTH] request_token received")

    # ---------------------------------------------------------
    # 3️⃣ Exchange request_token → access_token
    # ---------------------------------------------------------
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    _save_session(token_path, api_key, access_token)

    print("[AUTH] Access token generated and saved")

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    return kite
def get_kite_client(
    api_key: str,
    api_secret: str,
    *,
    token_path: str = TOKEN_PATH_DEFAULT,
    redirect_url: str = REDIRECT_URL_DEFAULT,
    timeout_seconds: int = 180,
):
    """
    Returns an authenticated KiteConnect client.

    Flow:
    1. Reuse today's saved access_token if available
    2. Else:
        - Open Zerodha login via Selenium
        - Auto-fill user id, password, TOTP
        - Capture request_token
        - Exchange for access_token
        - Save session
    """

    from kiteconnect import KiteConnect
    import os
    import time

    from kite_selenium_login import selenium_login_and_get_request_token

    print("[AUTH] Initializing Kite authentication")

    # ---------------------------------------------------------
    # 1️⃣ Try reusing today's saved session
    # ---------------------------------------------------------
    saved = _load_session(token_path)
    if saved:
        print("[AUTH] Using saved access token")
        kite = KiteConnect(api_key=saved["api_key"])
        kite.set_access_token(saved["access_token"])
        return kite

    # ---------------------------------------------------------
    # 2️⃣ Fresh login using Selenium
    # ---------------------------------------------------------
    print("[AUTH] Saved token missing/expired → starting auto login")

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()

    user_id = os.getenv("KITE_USER_ID")
    password = os.getenv("KITE_PASSWORD")
    totp_secret = os.getenv("KITE_TOTP_SECRET")

    if not user_id or not password or not totp_secret:
        raise RuntimeError(
            "❌ Missing one of KITE_USER_ID / KITE_PASSWORD / KITE_TOTP_SECRET in environment"
        )

    # --- Selenium handles Zerodha login ---
    request_token = selenium_login_and_get_request_token(
        login_url=login_url,
        user_id=user_id,
        password=password,
        totp_secret=totp_secret,
        timeout=timeout_seconds,
    )

    if not request_token:
        raise RuntimeError("❌ Failed to obtain request_token from Zerodha")

    print("[AUTH] request_token received")

    # ---------------------------------------------------------
    # 3️⃣ Exchange request_token → access_token
    # ---------------------------------------------------------
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    _save_session(token_path, api_key, access_token)

    print("[AUTH] Access token generated and saved")

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    return kite

def finish_login_with_request_token(api_key: str, api_secret: str, request_token: str, token_path: str = TOKEN_PATH_DEFAULT):
    kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]
    _save_session(token_path, api_key, access_token)
    kite.set_access_token(access_token)
    return kite