# -*- coding: utf-8 -*-
"""Съём фактов для «1 — Карта машины»: где лежит история и настройки трёх сред агентов на этой машине.

Только чтение. Секретов не читает и не печатает: файлы входа (.credentials.json, auth.json, .env) не открываются;
из настроек агентов берутся только имена серверов и одно значение cleanupPeriodDays. Личные проекты печатаются
только числом. Запуск: py -3 -X utf8 snapshot_machine.py
"""
import datetime as dt, glob, io, json, os, re, shutil, sqlite3, subprocess
from collections import Counter

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
CODEX = os.path.join(HOME, ".codex")
CURSOR = os.path.join(HOME, ".cursor")
ROAM = os.path.join(HOME, "AppData", "Roaming")
COMPANY_PREFIX = "c--Users-redmi-Breezeks-Git"
CANON_PREFIX = ("D--Knowledge-OS", "d--Knowledge-OS")
SKIP_NAMES = {".credentials.json", "auth.json", ".env"}


def size_of(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def gb(n):
    return f"{n / 1024 ** 3:.2f} ГБ" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} МБ"


def first_last_ts(path):
    first = entry = None
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i > 200:
                break
            try:
                r = json.loads(line)
            except Exception:
                continue
            first = first or r.get("timestamp")
            entry = entry or r.get("entrypoint")
            if first and entry:
                break
    last = None
    with open(path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 262144))
        for line in f.read().decode("utf-8", "replace").splitlines()[::-1]:
            m = re.search(r'"timestamp":"([^"]+)"', line)
            if m:
                last = m.group(1)
                break
    return first, last, entry


def category(folder):
    if folder.startswith(COMPANY_PREFIX):
        return "рабочая папка компании"
    if folder.startswith(CANON_PREFIX):
        return "канон"
    if "Temp" in folder or "AppData" in folder or "tmp" in folder.lower():
        return "временные"
    return "прочие проекты"


def section(title):
    print(f"\n== {title}")


# 1. Claude Code
section("1. Claude Code — ~/.claude")
print("объём папки:", gb(size_of(CLAUDE)))
sessions, months, entries, cats, big, sizes = [], Counter(), Counter(), Counter(), 0, []
last_months = Counter()
for p in glob.glob(os.path.join(CLAUDE, "projects", "*", "*.jsonl")):
    folder = os.path.basename(os.path.dirname(p))
    s = os.path.getsize(p)
    sizes.append(s)
    big += s > 10 * 1024 ** 2
    fts, lts, ep = first_last_ts(p)
    months[(fts or "????-??")[:7]] += 1
    last_months[(lts or "????-??")[:7]] += 1
    entries[ep or "нет поля"] += 1
    cats[category(folder)] += 1
print("сессий:", len(sizes), "| объём:", gb(sum(sizes)), "| крупнее 10 МБ:", big, "| самая большая:", gb(max(sizes)) if sizes else "—")
print("по месту:", dict(cats))
print("по месяцу начала:", dict(sorted(months.items())))
print("по месяцу последней записи:", dict(sorted(last_months.items())))
print("откуда запущены:", dict(entries))
folders = [d for d in glob.glob(os.path.join(CLAUDE, "projects", "*")) if os.path.isdir(d)]
print("папок проектов:", len(folders), "| по месту:", dict(Counter(category(os.path.basename(d)) for d in folders)))
mem = Counter()
for d in folders:
    n = len(glob.glob(os.path.join(d, "memory", "**", "*.md"), recursive=True))
    if n:
        mem[category(os.path.basename(d))] += n
print("файлов памяти:", sum(mem.values()), dict(mem))
st = io.open(os.path.join(CLAUDE, "settings.json"), encoding="utf-8").read()
m = re.search(r'"cleanupPeriodDays"\s*:\s*(\d+)', st)
print("cleanupPeriodDays:", m.group(1) if m else "не задан")
skills = [e for e in os.scandir(os.path.join(CLAUDE, "skills"))]
kinds = Counter()
targets = Counter()
for e in skills:
    full = e.path
    if e.name in ("synced", ".trash"):
        kinds["служебные"] += 1
    elif os.path.islink(full) or (hasattr(os.path, "isjunction") and os.path.isjunction(full)):
        kinds["ссылки"] += 1
        try:
            t = os.readlink(full)
        except OSError:
            t = ""
        targets["канон" if "Knowledge-OS" in t else ("Второй мозг компании" if "Breezeks-Git" in t else ("~/.agents/skills" if ".agents" in t else "другое"))] += 1
    elif e.is_dir():
        kinds["папки-копии"] += 1
print("навыки:", dict(kinds), "| куда ведут ссылки:", dict(targets))
cj = os.path.join(HOME, ".claude.json")
try:
    cfg = json.load(io.open(cj, encoding="utf-8"))
    names = sorted(cfg.get("mcpServers", {}).keys())
    print("MCP уровня пользователя в ~/.claude.json:", len(names), names)
    proj = cfg.get("projects", {})
    print("папок проектов в ~/.claude.json:", len(proj))
except Exception as e:
    print("~/.claude.json:", type(e).__name__)
for sub in ("file-history", "downloads", "plugins"):
    p = os.path.join(CLAUDE, sub)
    if os.path.isdir(p):
        print(f"{sub}:", gb(size_of(p)))
ip = os.path.join(CLAUDE, "plugins", "installed_plugins.json")
if os.path.exists(ip):
    try:
        j = json.load(io.open(ip, encoding="utf-8"))
        print("плагинов установлено:", len(j.get("plugins", j)))
    except Exception as e:
        print("installed_plugins.json:", type(e).__name__)

# 2. Настольное приложение Claude
section("2. Настольное приложение Claude — AppData\\Roaming\\Claude")
cd = os.path.join(ROAM, "Claude")
la = glob.glob(os.path.join(cd, "local-agent-mode-sessions", "**", "*"), recursive=True)
la = [p for p in la if os.path.isfile(p)]
if la:
    mt = sorted(dt.datetime.fromtimestamp(os.path.getmtime(p)).date() for p in la)
    print("local-agent-mode-sessions: файлов", len(la), "| с", mt[0], "по", mt[-1])
cc = glob.glob(os.path.join(cd, "claude-code-sessions", "**", "*"), recursive=True)
print("claude-code-sessions: файлов", sum(1 for p in cc if os.path.isfile(p)))
try:
    dc = json.load(io.open(os.path.join(cd, "claude_desktop_config.json"), encoding="utf-8"))
    print("MCP приложения:", sorted(dc.get("mcpServers", {}).keys()))
except Exception as e:
    print("claude_desktop_config.json:", type(e).__name__)
for p in glob.glob(os.path.join(cd, "**", "scheduled-tasks.json"), recursive=True):
    try:
        j = json.load(io.open(p, encoding="utf-8"))
        print("расписаний:", len(j) if isinstance(j, list) else len(j.get("tasks", j)))
    except Exception as e:
        print("scheduled-tasks.json:", type(e).__name__)

# 3. Codex
section("3. Codex — ~/.codex")
print("объём папки:", gb(size_of(CODEX)))
cs = glob.glob(os.path.join(CODEX, "sessions", "**", "*.jsonl"), recursive=True)
bym = Counter()
for p in cs:
    m = re.search(r"sessions[\\/](\d{4})[\\/](\d\d)", p)
    bym[f"{m.group(1)}-{m.group(2)}" if m else "?"] += 1
print("сессий:", len(cs), "| по месяцам:", dict(sorted(bym.items())))
for db in glob.glob(os.path.join(CODEX, "*.sqlite")):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
        counts = {}
        for t in tabs:
            if t in ("threads", "external_agent_config_imports", "memories"):
                counts[t] = con.execute(f'select count(*) from "{t}"').fetchone()[0]
        print(os.path.basename(db), "→", counts or f"таблиц {len(tabs)}")
        con.close()
    except Exception as e:
        print(os.path.basename(db), "→", type(e).__name__)
imp = os.path.join(CODEX, "external_agent_session_imports.json")
if os.path.exists(imp):
    try:
        j = json.load(io.open(imp, encoding="utf-8"))
        items = j if isinstance(j, list) else j.get("imports", j)
        print("импортировано чужих сессий:", len(items))
    except Exception as e:
        print("imports:", type(e).__name__)
toml = io.open(os.path.join(CODEX, "config.toml"), encoding="utf-8").read()
srv = sorted(set(re.findall(r"(?m)^\[mcp_servers\.([A-Za-z0-9_\-\"]+)\]", toml)))
print("MCP в config.toml:", len(srv))
ag = os.path.join(CODEX, "AGENTS.md")
if os.path.exists(ag):
    a = io.open(ag, encoding="utf-8").read()
    print("~/.codex/AGENTS.md: ссылка на ~/.Codex/kos —", "~/.Codex/kos" in a or ".codex/kos" in a.lower(), "| папка ~/.codex/kos есть —", os.path.exists(os.path.join(CODEX, "kos")))
print("навыков в ~/.codex/skills:", len([d for d in glob.glob(os.path.join(CODEX, "skills", "*")) if os.path.isdir(d)]))
print("файлов в ~/.codex/memories:", len(glob.glob(os.path.join(CODEX, "memories", "**", "*"), recursive=True)))
ags = os.path.join(HOME, ".agents", "skills")
print("~/.agents/skills: записей", len(os.listdir(ags)) if os.path.isdir(ags) else "нет папки")

# 4. Cursor
section("4. Cursor")
ext = sorted(os.path.basename(d) for d in glob.glob(os.path.join(CURSOR, "extensions", "*")) if re.match(r"(anthropic\.claude-code|openai\.chatgpt)-", os.path.basename(d)))
print("расширения агентов:", ext)
gs = os.path.join(ROAM, "Cursor", "User", "globalStorage")
for p in glob.glob(os.path.join(gs, "state.vscdb*")):
    print(os.path.basename(p), gb(os.path.getsize(p)), dt.datetime.fromtimestamp(os.path.getmtime(p)).date())
ws = [d for d in glob.glob(os.path.join(ROAM, "Cursor", "User", "workspaceStorage", "*")) if os.path.isdir(d)]
recent = [d for d in ws if dt.datetime.fromtimestamp(os.path.getmtime(d)) >= dt.datetime(2026, 10, 18)]
print("рабочих областей:", len(ws), "| изменялись после 18.10:", len(recent))
tr = glob.glob(os.path.join(CURSOR, "projects", "*", "agent-transcripts", "**", "*"), recursive=True)
tr = [p for p in tr if os.path.isfile(p)]
print("транскриптов агента Cursor:", len(tr), "в папках:", len({p.split(os.sep + "agent-transcripts")[0] for p in tr}),
      "| всего папок в ~/.cursor/projects:", len(glob.glob(os.path.join(CURSOR, "projects", "*"))))
try:
    mj = json.load(io.open(os.path.join(CURSOR, "mcp.json"), encoding="utf-8"))
    print("MCP в ~/.cursor/mcp.json:", len(mj.get("mcpServers", {})))
except Exception as e:
    print("mcp.json:", type(e).__name__)
print("планов:", len(glob.glob(os.path.join(CURSOR, "plans", "*"))))

# 5. Проекты и диски
section("5. Проекты и диски")
bg = r"C:\Users\redmi\Breezeks-Git"
repos = [os.path.dirname(p) for p in glob.glob(bg + r"\**\.git", recursive=True) if ".pub-" not in p and "_Публикация" not in p]
print("репозиториев в рабочей папке компании:", len(repos))
k = subprocess.run(["git", "-C", r"D:\Knowledge-OS", "status", "-sb", "--porcelain"], capture_output=True, text=True, encoding="utf-8").stdout.splitlines()
print("канон:", k[0] if k else "?", "| незакоммиченных путей:", max(0, len(k) - 1))
for d in ("C:\\", "D:\\"):
    u = shutil.disk_usage(d)
    print(f"диск {d} всего {u.total / 1024 ** 3:.0f} ГБ, свободно {u.free / 1024 ** 3:.0f} ГБ ({100 * u.used / u.total:.0f} % занято)")
print("\nснято:", dt.datetime.now().strftime("%d.%m.%Y %H:%M"))
