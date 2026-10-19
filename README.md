# VitaDock → Garmin Connect sync

This project copies your Medisana weigh-ins from VitaDock Online (cloud.vitadock.com) to Garmin Connect automatically, once a day. It syncs these values:

- weight
- fat %
- water %
- muscle mass (in kg)
- bone mass
- BMI
- BMR

The sync runs as a GitHub Action. That works in a **public** repository too: passwords, tokens and measurement values never end up in the code or the logs.

## How it works

1. The script logs in to VitaDock Online with your own account. This is the same login the website uses, not an official API.
2. It fetches all your weigh-ins as JSON.
3. It checks which weigh-ins are already in Garmin and skips measurements whose time and weight roughly match (within 2 minutes and 150 g). This prevents duplicates.
4. It uploads the new measurements as FIT weigh-ins with [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect).

## Security in a public repo

| Risk | Mitigation |
|---|---|
| Password or tokens in the code | Everything is stored in **environment secrets**. `.env` and token files are listed in `.gitignore`. |
| Secrets in the logs | GitHub masks secrets. The script itself never prints secrets or tokens. |
| Measurement values in the (public) logs | The script runs in **private mode**: it logs only counts, no weights or dates. Debug logging is off. |
| A fork or PR stealing your secrets | The workflow starts **only** on schedule or manually (`workflow_dispatch`), never on `pull_request` or `push`. The secrets live in an environment restricted to `main`. |
| Script injection through the input fields | Inputs reach the script through `env` and are never interpolated into shell code. `--since` is strictly validated. |
| Supply chain (hijacked packages or actions) | Actions are pinned to a commit SHA. Python packages are installed only with `--require-hashes` from `requirements.lock`. |
| Tokens in caches or artifacts | Nothing is cached or uploaded as an artifact. The token file lives in `$RUNNER_TEMP` and is deleted afterwards. |
| Refreshed Garmin tokens | An optional PAT writes refreshed tokens back to the secret. That PAT is restricted to this one repo. |

## One-time setup

### 1. Locally: create Garmin tokens (on your own computer, with MFA)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:GARMIN_EMAIL="you@example.com"; $env:GARMIN_PASSWORD="..."
.venv\Scripts\python vitadock_garmin_sync.py --garmin-login
```

The tokens are stored in `%USERPROFILE%\.garminconnect\garmin_tokens.json`. Your Garmin password never goes to GitHub; the Action only uses the tokens.

### 2. Repository and environment

1. Create a new (public) repo and push this folder.
2. Go to **Settings → Environments → New environment** and create `garmin-sync`.
3. Under *Deployment branches and tags*, choose **Selected branches** and allow only `main`.
4. Go to **Settings → Actions → General** and, under *Fork pull request workflows*, choose **Require approval for all external contributors**.

### 3. Secrets in the `garmin-sync` environment

With the [GitHub CLI](https://cli.github.com/), from the repo folder:

```powershell
gh secret set VITADOCK_EMAIL    --env garmin-sync
gh secret set VITADOCK_PASSWORD --env garmin-sync
Get-Content "$env:USERPROFILE\.garminconnect\garmin_tokens.json" -Raw | gh secret set GARMIN_TOKENS --env garmin-sync
```

`gh secret set` prompts for the value, so it does not end up in your shell history.

### 4. (Recommended) Let tokens refresh automatically

Garmin may issue a new refresh token when the tokens are refreshed. To keep it:

1. Create a **fine-grained personal access token**:
   - Repository access: **only this repo**.
   - Permissions: **Environments: Read and write**. If you get a 403 error, also add **Secrets: Read and write**.
   - Choose an expiration date, for example 1 year.
2. Store it as the secret `TOKEN_UPDATE_PAT` in the `garmin-sync` environment.

The sync also works without this PAT, but then you have to repeat step 1 and the `GARMIN_TOKENS` line whenever the tokens expire. The run fails with a clear message when that happens.

### 5. Testing

Go to **Actions → VitaDock → Garmin sync → Run workflow**:

1. First run it with **dry_run** checked.
2. Then run it once with `since = 2018-05-01` to copy your history.

After that, the sync runs every day at 10:07 Amsterdam time (`timezone: Europe/Amsterdam`).

## Running locally (optional)

Copy `.env.example` to `.env` and fill it in. Then:

```powershell
.venv\Scripts\python vitadock_garmin_sync.py --dry-run
```

`run_sync.bat` / `run_sync.sh` are there for Windows Task Scheduler or cron.

## Good to know

- If Medisana changes the VitaDock website, the login may stop working. The run then fails visibly, and GitHub emails you.
- Garmin sometimes blocks logins from data-center IPs (such as GitHub runners). That is why the first login is done locally and the Action only uses tokens.
- GitHub disables scheduled workflows in public repos after 60 days without activity. The keepalive step re-enables the workflow on every run, without commits.
