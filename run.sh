#!/usr/bin/env bash
# One-command setup + start for GitHub Codespaces / Ubuntu / Debian.   Usage:  bash run.sh
set -e
cd "$(dirname "$0")"
SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"
pgsu() { if [ "$(id -u)" = "0" ]; then su postgres -c "$1"; else sudo -u postgres bash -c "$1"; fi; }

echo "==> 1/5 PostgreSQL"
if ! command -v psql >/dev/null 2>&1; then
  $SUDO apt-get update -qq
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql >/dev/null
fi
$SUDO service postgresql start >/dev/null 2>&1 || true
for i in $(seq 1 20); do pgsu "pg_isready -q" && break; sleep 1; done
pgsu "psql -qc \"ALTER USER postgres PASSWORD 'postgres';\"" >/dev/null
pgsu "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='ev_analytics'\"" | grep -q 1 \
  || pgsu "createdb ev_analytics"

echo "==> 2/5 Python packages"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt

echo "==> 3/5 Settings"
[ -f .env ] || cp .env.example .env

echo "==> 4/5 Database tables + simulated data"
if python database.py --check >/dev/null 2>&1 && python - <<'PY'
import database as db
raise SystemExit(0 if db.table_counts().get("stations", 0) > 0 else 1)
PY
then echo "    data already loaded"; else python database.py --reset | tail -1; fi

echo "==> 5/5 Starting the app (open the link from the Ports tab / popup)"
exec streamlit run app.py --server.port 8501 --server.headless true
