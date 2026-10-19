#!/usr/bin/env python3
"""
VitaDock Online  ->  Garmin Connect weigh-in synchronisation.

Fetches all weigh-ins from VitaDock Online (cloud.vitadock.com, using your
regular VitaDock login) and uploads the measurements that are not yet in
Garmin Connect as body-composition weigh-ins (weight, fat %, water %,
muscle mass, bone mass, BMI, BMR).

Idempotent: the script first queries the existing Garmin weigh-ins in the
sync window and skips measurements that are already there. It can therefore
safely run every day (or more often), even from an environment without
persistent storage.

Configuration via environment variables (or a .env file next to the script):
  VITADOCK_EMAIL, VITADOCK_PASSWORD
  GARMIN_EMAIL, GARMIN_PASSWORD      (only needed for the first login or when tokens expire)
  GARMINTOKENS                        path to token file/directory OR the token JSON itself
                                      (default: ~/.garminconnect)

Usage:
  python vitadock_garmin_sync.py --garmin-login        # one-off, interactive (MFA)
  python vitadock_garmin_sync.py --dry-run             # show what would be uploaded
  python vitadock_garmin_sync.py                       # sync the last 14 days
  python vitadock_garmin_sync.py --since 2024-01-01    # backfill history
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

VITADOCK_BASE = "https://cloud.vitadock.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
DEFAULT_TOKENSTORE = "~/.garminconnect"
MATCH_TOLERANCE_S = 120      # same measurement if the time is within 2 min ...
MATCH_WEIGHT_KG = 0.15       # ... and the weight within 150 g

log = logging.getLogger("vitadock-garmin-sync")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=value); does not override existing env vars."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def env(name: str, required: bool = True) -> str | None:
    value = os.environ.get(name)
    if required and not value:
        sys.exit(f"Missing environment variable: {name}")
    return value


# --------------------------------------------------------------------------- #
# VitaDock
# --------------------------------------------------------------------------- #
@dataclass
class Measurement:
    ts: datetime          # aware, UTC
    weight: float         # kg
    fat_pct: float | None
    water_pct: float | None
    muscle_pct: float | None
    bone_kg: float | None
    bmi: float | None
    kcal: float | None

    @property
    def muscle_kg(self) -> float | None:
        if self.muscle_pct is None:
            return None
        return round(self.weight * self.muscle_pct / 100.0, 2)


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def parse_vitadock(payload: dict) -> list[Measurement]:
    seen: set[int] = set()
    out: list[Measurement] = []
    for row in payload.get("bodyWeight") or []:
        ms = row.get("x")
        weight = _num(row.get("y"))
        if not ms or not weight or ms in seen:
            continue
        seen.add(ms)
        out.append(
            Measurement(
                ts=datetime.fromtimestamp(ms / 1000, tz=timezone.utc),
                weight=weight,
                fat_pct=_num(row.get("bodyFat")),
                water_pct=_num(row.get("bodyWater")),
                muscle_pct=_num(row.get("muscleMass")),
                bone_kg=_num(row.get("boneMass")),
                bmi=_num(row.get("bmi")),
                kcal=_num(row.get("kcal")),
            )
        )
    out.sort(key=lambda m: m.ts)
    return out


def fetch_vitadock(email: str, password: str) -> list[Measurement]:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT

    # 1. Fetch the login page for the CSRF token + session cookie
    r = s.get(f"{VITADOCK_BASE}/signin", timeout=30)
    r.raise_for_status()
    m = re.search(r'name="_csrf"\s+value="([^"]+)"', r.text) or re.search(
        r'value="([^"]+)"\s+name="_csrf"', r.text
    )
    if not m:
        raise RuntimeError("VitaDock: CSRF token not found on /signin")

    # 2. Log in (Spring Security form login)
    r = s.post(
        f"{VITADOCK_BASE}/j_spring_security_check",
        data={
            "j_username": email,
            "j_password": password,
            "_csrf": m.group(1),
            "oauth_token": "",
            "marketingid": "",
            "code": "",
        },
        timeout=30,
        allow_redirects=True,
    )
    r.raise_for_status()
    if "/signin" in r.url and ("error" in r.url or "j_password" in r.text):
        raise RuntimeError("VitaDock: login failed (check email/password)")

    # 3. Open the weight module: sets the portal session (redirects to /portal/target.php)
    r = s.get(f"{VITADOCK_BASE}/module/target", timeout=30)
    r.raise_for_status()

    # 4. All weight data as JSON
    r = s.get(
        f"{VITADOCK_BASE}/portal/server/target_server.php",
        params={"lang": "en_GB", "rnd": int(time.time())},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        timeout=60,
    )
    r.raise_for_status()
    try:
        payload = r.json()
    except ValueError as e:
        raise RuntimeError(
            "VitaDock: no JSON received from target_server.php "
            "(invalid session or the site has changed)"
        ) from e
    return parse_vitadock(payload)


# --------------------------------------------------------------------------- #
# Garmin
# --------------------------------------------------------------------------- #
def garmin_client(interactive: bool = False):
    from garminconnect import Garmin  # lazy import, so --help works without it

    tokenstore = os.environ.get("GARMINTOKENS") or DEFAULT_TOKENSTORE
    inline = tokenstore.lstrip().startswith("{")
    email = env("GARMIN_EMAIL", required=interactive)
    password = env("GARMIN_PASSWORD", required=interactive)
    prompt = (lambda: input("Garmin MFA code: ").strip()) if interactive else None

    api = Garmin(email=email, password=password, prompt_mfa=prompt)
    if interactive:
        # Always write to a file so the tokens are kept
        path = Path(DEFAULT_TOKENSTORE if inline else tokenstore).expanduser()
        api.login(tokenstore=str(path))
        return api, path
    try:
        api.login(tokenstore=tokenstore)
    except Exception as e:  # noqa: BLE001
        sys.exit(
            f"Garmin login failed ({type(e).__name__}). Tokens are missing or expired: "
            "run 'python vitadock_garmin_sync.py --garmin-login' locally and update the "
            "GARMIN_TOKENS secret (see README)."
        )
    return api, None


def garmin_existing(api, start: datetime, end: datetime) -> list[tuple[float, float]]:
    """(epoch seconds UTC, kg) of existing Garmin weigh-ins in the window."""
    data = api.get_body_composition(
        (start - timedelta(days=1)).date().isoformat(),
        (end + timedelta(days=1)).date().isoformat(),
    ) or {}
    out = []
    for item in data.get("dateWeightList") or []:
        ts = item.get("timestampGMT") or item.get("date")
        grams = item.get("weight")
        if ts and grams:
            out.append((ts / 1000.0, grams / 1000.0))
    return out


def already_in_garmin(m: Measurement, existing: list[tuple[float, float]]) -> bool:
    t = m.ts.timestamp()
    return any(
        abs(t - ets) <= MATCH_TOLERANCE_S and abs(m.weight - ekg) <= MATCH_WEIGHT_KG
        for ets, ekg in existing
    )


def upload(api, m: Measurement) -> None:
    api.add_body_composition(
        timestamp=m.ts.isoformat(),
        weight=m.weight,
        percent_fat=m.fat_pct,
        percent_hydration=m.water_pct,
        bone_mass=m.bone_kg,
        muscle_mass=m.muscle_kg,
        basal_met=m.kcal,
        bmi=m.bmi,
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sync VitaDock Online weigh-ins to Garmin Connect")
    p.add_argument("--days", type=int, default=14, help="number of days to look back (default 14)")
    p.add_argument("--since", help="sync everything from this date (YYYY-MM-DD), for backfills")
    p.add_argument("--dry-run", action="store_true", help="upload nothing, only show")
    p.add_argument("--garmin-login", action="store_true",
                   help="one-off interactive Garmin login (MFA) and store tokens")
    p.add_argument("--private", action="store_true",
                   help="do not log measurement values/dates (on by default in GitHub Actions)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    private = args.private or os.environ.get("GITHUB_ACTIONS") == "true"
    verbose = args.verbose and not private   # debug logs may contain data
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    for noisy in ("garminconnect", "urllib3", "requests", "curl_cffi"):
        logging.getLogger(noisy).setLevel(logging.DEBUG if verbose else logging.WARNING)
    load_dotenv(Path(__file__).with_name(".env"))

    if args.garmin_login:
        api, path = garmin_client(interactive=True)
        log.info("Garmin login succeeded, tokens stored in %s", path)
        return 0

    now = datetime.now(timezone.utc)
    try:
        start = (
            datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if args.since else now - timedelta(days=args.days)
        )
    except ValueError:
        sys.exit("--since must have the format YYYY-MM-DD")

    measurements = fetch_vitadock(env("VITADOCK_EMAIL"), env("VITADOCK_PASSWORD"))
    if private:
        log.info("VitaDock: %d measurements in total", len(measurements))
    else:
        log.info("VitaDock: %d measurements in total, latest at %s", len(measurements),
                 measurements[-1].ts.isoformat() if measurements else "-")
    window = [m for m in measurements if m.ts >= start]
    log.info("%d measurements in the sync window", len(window))
    if not window:
        log.info("Nothing to sync.")
        return 0

    api, _ = garmin_client()
    existing = garmin_existing(api, window[0].ts, now)
    todo = [m for m in window if not already_in_garmin(m, existing)]
    log.info("Garmin: %d existing weigh-ins in window, %d new to upload",
             len(existing), len(todo))

    failures = 0
    for i, m in enumerate(todo, 1):
        desc = (f"measurement {i}/{len(todo)}" if private else
                f"{m.ts.astimezone():%Y-%m-%d %H:%M}  {m.weight:.1f} kg  "
                f"fat {m.fat_pct}%  water {m.water_pct}%  muscle {m.muscle_kg} kg  "
                f"bone {m.bone_kg} kg  BMI {m.bmi}")
        if args.dry_run:
            log.info("[dry-run] %s", desc)
            continue
        try:
            upload(api, m)
            log.info("Uploaded: %s", desc)
            time.sleep(1.5)
        except Exception as e:  # noqa: BLE001 - continue with the rest
            failures += 1
            log.error("Upload failed for %s: %s: %s", desc, type(e).__name__, str(e)[:200])

    summary = {"vitadock_total": len(measurements), "window": len(window),
               "uploaded": 0 if args.dry_run else len(todo) - failures,
               "skipped_existing": len(window) - len(todo), "failed": failures,
               "dry_run": args.dry_run}
    print(json.dumps(summary))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
