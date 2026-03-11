import time
import pyotp
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import urlparse, parse_qs
from selenium.common.exceptions import TimeoutException


def selenium_login_and_get_request_token(
    login_url: str,
    user_id: str,
    password: str,
    totp_secret: str,
    timeout: int = 180
) -> str:

    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=800,600")
    options.add_argument("--window-position=50,50")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    wait = WebDriverWait(driver, 30)

    driver.get(login_url)

    try:
        # -------------------------------------------------
        # 1️⃣ Wait for login form (handles async load)
        # -------------------------------------------------
        try:
            userid_el = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']"))
            )
        except TimeoutException:
            raise RuntimeError("❌ Login page did not load userid field")

        # Zerodha currently uses first text input for userid
        userid_el.clear()
        userid_el.send_keys(user_id)

        # Password field
        pwd_el = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
        )
        pwd_el.clear()
        pwd_el.send_keys(password)

        # Login button
        login_btn = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))
        )
        login_btn.click()

        from selenium.webdriver.common.keys import Keys

        # -------------------------------------------------
        # 2️⃣ TOTP – Zerodha AUTO-SUBMIT (NO CLICK)
        # -------------------------------------------------
        print("[AUTH] Waiting for TOTP screen...")

        totp_input = wait.until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "input[type='number'], input[type='text']")
            )
        )

        # Generate OTP safely (avoid last seconds)
        totp = pyotp.TOTP(totp_secret)
        remaining = totp.interval - (time.time() % totp.interval)
        if remaining < 5:
            print("[AUTH] OTP window about to expire, waiting...")
            time.sleep(remaining + 1)

        otp_code = totp.now()
        print("[AUTH] Generated OTP")

        # Focus & type OTP ONLY
        driver.execute_script("arguments[0].focus();", totp_input)
        totp_input.clear()
        totp_input.send_keys(otp_code)

        print("[AUTH] OTP entered, waiting for auto redirect...")

        # -------------------------------------------------
        # 3️⃣ WAIT FOR AUTO REDIRECT WITH request_token
        # -------------------------------------------------
        start = time.time()
        while time.time() - start < timeout:
            current_url = driver.current_url

            if "request_token=" in current_url:
                parsed = urlparse(current_url)
                token = parse_qs(parsed.query).get("request_token", [None])[0]
                if token:
                    print("[AUTH] request_token received")
                    return token

            time.sleep(0.5)

        raise RuntimeError("❌ request_token not received after OTP")


    finally:
        driver.quit()
