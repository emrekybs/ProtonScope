#!/usr/bin/env python3

import asyncio
import aiohttp
import re
import json
import dns.resolver
import hashlib
from datetime import datetime, UTC

# ---------------------------
# COLORS
# ---------------------------
class C:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

# ---------------------------
TIMEOUT = aiohttp.ClientTimeout(total=10)
SEMAPHORE = asyncio.Semaphore(10)
PROTON_DOMAINS = ["protonmail.com", "protonmail.ch", "proton.me", "pm.me"]

results = {
    "input": {},
    "proton": {},
    "dns": {},
    "footprint": {},
    "accounts": {},
    "summary": {}
}

# ---------------------------
def banner():
    print(f"""{C.GREEN}{C.BOLD}
╔══════════════════════════════════════════════╗
║              ProtonScope v1.0                ║
║     Advanced Email OSINT Intelligence        ║
║                                              ║
║  Author : Emre Koybasi                       ║
║  GitHub : https://github.com/emrekybs        ║
╚══════════════════════════════════════════════╝
{C.RESET}""")

# ---------------------------
def get_email():
    email = input(f"{C.CYAN}Target Email > {C.RESET}").strip()
    while "@" not in email:
        print(f"{C.RED}Invalid email!{C.RESET}")
        email = input(f"{C.CYAN}Target Email > {C.RESET}").strip()
    return email

# ---------------------------
def generate_variations(email):
    user = email.split("@")[0]
    variations = [f"{user}@{d}" for d in PROTON_DOMAINS]
    variations += [
        f"{user}+1@proton.me",
        f"{user}.1@proton.me",
        f"{user}_tr@proton.me"
    ]
    results["proton"]["variations"] = variations

# ---------------------------
async def safe_request(session, method, url, **kwargs):
    async with SEMAPHORE:
        try:
            async with session.request(method, url, timeout=TIMEOUT, **kwargs) as r:
                return r.status, await r.text()
        except:
            return None, None

# ---------------------------
async def proton_pgp(session, email):
    url = f"https://api.protonmail.ch/pks/lookup?op=index&search={email}"
    _, text = await safe_request(session, "GET", url)

    if not text or "info:1:1" not in text:
        return

    pgp = {}

    emails = re.findall(r'<(.*?)>', text)
    if emails:
        pgp["uids"] = emails

    ts = re.findall(r':(\d{10}):', text)
    if ts:
        pgp["created"] = datetime.fromtimestamp(int(ts[0]), UTC).isoformat()

    results["proton"]["pgp"] = pgp

# ---------------------------
def dns_check(domain):
    try:
        mx = dns.resolver.resolve(domain, 'MX')
        mx_records = [str(x.exchange).lower() for x in mx]
        results["dns"]["mx"] = mx_records
        results["dns"]["uses_proton"] = any("proton" in x for x in mx_records)
    except:
        results["dns"]["mx"] = []
        results["dns"]["uses_proton"] = False

# ---------------------------
async def gravatar(session, email):
    h = hashlib.md5(email.lower().encode()).hexdigest()
    url = f"https://www.gravatar.com/avatar/{h}?d=404"
    status, _ = await safe_request(session, "GET", url)

    if status == 200:
        results["footprint"]["gravatar"] = "found"
    elif status == 404:
        results["footprint"]["gravatar"] = "not_found"
    else:
        results["footprint"]["gravatar"] = "unknown"

# ---------------------------
async def check_spotify(session, email):
    _, text = await safe_request(session, "GET",
        "https://spclient.wg.spotify.com/signup/public/v1/account",
        params={"validate": 1, "email": email}
    )
    return "confirmed" if text and "already" in text.lower() else "unknown"

async def check_twitter(session, email):
    _, text = await safe_request(session, "GET",
        "https://api.twitter.com/i/users/email_available.json",
        params={"email": email}
    )
    if text:
        if "taken" in text.lower():
            return "confirmed"
        if "available" in text.lower():
            return "not_found"
    return "unknown"

async def check_instagram(session, email):
    _, text = await safe_request(session, "POST",
        "https://www.instagram.com/accounts/account_recovery_send_ajax/",
        data={"email_or_username": email}
    )
    if text:
        if "sent" in text.lower():
            return "confirmed"
        if "no users found" in text.lower():
            return "not_found"
    return "unknown"

async def check_discord(session, email):
    _, text = await safe_request(session, "POST",
        "https://discord.com/api/v9/auth/forgot",
        json={"login": email}
    )
    if text:
        if "email sent" in text.lower():
            return "confirmed"
        if "invalid" in text.lower():
            return "not_found"
    return "unknown"

async def check_google(session, email):
    _, text = await safe_request(session, "GET",
        "https://accounts.google.com/_/lookup/accountlookup",
        params={"Email": email}
    )
    if text and "identifierexists" in text.lower():
        return "confirmed"
    return "unknown"

# ---------------------------
async def account_scan(session, email):
    tasks = {
        "spotify": check_spotify(session, email),
        "twitter": check_twitter(session, email),
        "instagram": check_instagram(session, email),
        "discord": check_discord(session, email),
        "google": check_google(session, email),
        "pinterest": asyncio.sleep(0, result="unknown"),
        "tumblr": asyncio.sleep(0, result="unknown")
    }
    results["accounts"] = {k: await v for k, v in tasks.items()}

# ---------------------------
def build_summary(email):
    confirmed = [k for k, v in results["accounts"].items() if v == "confirmed"]
    score = len(confirmed) * 15

    results["summary"] = {
        "email": email,
        "confirmed_accounts": confirmed,
        "likely_accounts": [],
        "risk_score": score
    }

# ---------------------------
def color_json(data):
    text = json.dumps(data, indent=4)
    text = re.sub(r'\"(.*?)\":', f'{C.CYAN}"\\1"{C.RESET}:', text)
    text = re.sub(r': \"(.*?)\"', f': {C.GREEN}"\\1"{C.RESET}', text)
    text = re.sub(r': (\d+)', f': {C.YELLOW}\\1{C.RESET}', text)
    text = re.sub(r'true', f'{C.GREEN}true{C.RESET}', text)
    text = re.sub(r'false', f'{C.RED}false{C.RESET}', text)
    return text

# ---------------------------
async def main():
    banner()

    email = get_email()
    domain = email.split("@")[1]

    results["input"]["email"] = email

    generate_variations(email)
    dns_check(domain)

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            proton_pgp(session, email),
            gravatar(session, email),
            account_scan(session, email)
        )

    build_summary(email)

    print(f"\n{C.GREEN}[+] RESULTS:{C.RESET}\n")
    print(color_json(results))
    print()

# ---------------------------
if __name__ == "__main__":
    asyncio.run(main())
