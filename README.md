# VitaDock → Garmin Connect sync

Dit script zet je Medisana-weegresultaten elke dag automatisch over van VitaDock Online (cloud.vitadock.com) naar Garmin Connect. Het neemt deze waarden mee:

- gewicht
- vet%
- water%
- spiermassa (in kg)
- botmassa
- BMI
- BMR

De sync draait als GitHub Action. Dat kan ook in een **publieke** repository: wachtwoorden, tokens en meetwaarden komen nooit in de code of in de logs.

## Hoe het werkt

1. Het script logt in op VitaDock Online met je eigen account. Dat is dezelfde login als op de website; het is geen officiële API.
2. Het haalt al je weegmetingen op als JSON.
3. Het kijkt welke weigh-ins al in Garmin staan en slaat metingen over waarvan tijd en gewicht ongeveer gelijk zijn (binnen 2 minuten en 150 g). Er ontstaan dus geen dubbele metingen.
4. Nieuwe metingen uploadt het als FIT-weigh-in met [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect).

## Beveiliging in een publieke repo

| Risico | Maatregel |
|---|---|
| Wachtwoord of tokens in de code | Alles staat in **environment secrets**. `.env` en tokenbestanden staan in `.gitignore`. |
| Secrets in de logs | GitHub maskeert secrets. Het script print zelf nooit secrets of tokens. |
| Meetwaarden in de (publieke) logs | Het script draait in **private mode**: alleen aantallen, geen gewichten of datums. Debug-logging staat uit. |
| Een fork of PR die je secrets steelt | De workflow start **alleen** op schema of handmatig (`workflow_dispatch`), nooit door `pull_request` of `push`. De secrets staan in een environment die beperkt is tot `main`. |
| Script-injectie via de invoervelden | Inputs gaan via `env` naar het script en worden niet in shellcode geïnterpoleerd. `--since` wordt streng gevalideerd. |
| Supply-chain (gekaapte packages of actions) | Actions zijn op commit-SHA vastgezet. Python-packages worden alleen geïnstalleerd met `--require-hashes` uit `requirements.lock`. |
| Tokens in cache of artifacts | Er wordt niets gecachet of als artifact geüpload. Het tokenbestand staat in `$RUNNER_TEMP` en wordt na afloop verwijderd. |
| Ververste Garmin-tokens | Een optionele PAT schrijft de ververste tokens terug naar het secret. Die PAT is beperkt tot deze ene repo. |

## Eenmalige setup

### 1. Lokaal: Garmin-tokens aanmaken (op je laptop, met MFA)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:GARMIN_EMAIL="jij@voorbeeld.nl"; $env:GARMIN_PASSWORD="..."
.venv\Scripts\python vitadock_garmin_sync.py --garmin-login
```

De tokens komen in `%USERPROFILE%\.garminconnect\garmin_tokens.json`. Je Garmin-wachtwoord hoeft niet naar GitHub; de Action gebruikt alleen de tokens.

### 2. Repository en environment

1. Maak een nieuwe (publieke) repo aan en push deze map.
2. Ga naar **Settings → Environments → New environment** en maak `garmin-sync` aan.
3. Zet bij *Deployment branches and tags* de optie **Selected branches** aan, met alleen `main`.
4. Ga naar **Settings → Actions → General** en kies bij *Fork pull request workflows* de optie **Require approval for all external contributors**.

### 3. Secrets in de environment `garmin-sync`

Met de [GitHub CLI](https://cli.github.com/), vanuit de repo-map:

```powershell
gh secret set VITADOCK_EMAIL    --env garmin-sync
gh secret set VITADOCK_PASSWORD --env garmin-sync
Get-Content "$env:USERPROFILE\.garminconnect\garmin_tokens.json" -Raw | gh secret set GARMIN_TOKENS --env garmin-sync
```

`gh secret set` vraagt om de waarde. Zo komt die niet in je shell-history.

### 4. (Aanbevolen) Tokens automatisch laten verversen

Garmin kan bij het verversen een nieuwe refresh-token uitgeven. Om die te bewaren:

1. Maak een **fine-grained personal access token** aan:
   - Repository access: **alleen deze repo**.
   - Permissions: **Environments: Read and write**. Krijg je een 403-fout, voeg dan ook **Secrets: Read and write** toe.
   - Kies een vervaldatum, bijvoorbeeld 1 jaar.
2. Sla die op als secret `TOKEN_UPDATE_PAT` in de environment `garmin-sync`.

Zonder deze PAT werkt de sync ook, maar dan moet je stap 1 en de `GARMIN_TOKENS`-regel opnieuw doen zodra de tokens verlopen. De run faalt dan met een duidelijke melding.

### 5. Testen

Ga naar **Actions → VitaDock → Garmin sync → Run workflow**:

1. Draai eerst met **dry_run** aangevinkt.
2. Draai daarna eenmalig met `since = 2018-05-01` om je historie over te zetten.

Daarna draait de sync elke dag om 06:17 UTC.

## Lokaal draaien (optioneel)

Kopieer `.env.example` naar `.env` en vul het in. Daarna:

```powershell
.venv\Scripts\python vitadock_garmin_sync.py --dry-run
```

`run_sync.bat` / `run_sync.sh` zijn er voor Windows Taakplanner of cron.

## Goed om te weten

- Als Medisana de VitaDock-website verandert, kan de login stoppen met werken. De run faalt dan zichtbaar, en GitHub mailt je.
- Garmin kan logins vanaf datacenter-IP's (GitHub-runners) soms blokkeren. Daarom wordt de eerste login lokaal gedaan en gebruikt de Action alleen tokens.
- GitHub zet geplande workflows in publieke repo's na 60 dagen zonder activiteit uit. De keepalive-stap zet de workflow elke run weer aan, zonder commits.
