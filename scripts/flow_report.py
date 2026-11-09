#!/usr/bin/env python3
"""One Page Report de sprint: métricas de fluxo (Vacanti) + previsão Monte Carlo (Magennis).

Uso:
  python flow_report.py analyze --csv export.csv --sprint-start 2026-09-15 --sprint-end 2026-09-26 \
      --sprint-name "Sprint 42" --team "Time Alfa" [--backlog 30] [--target-date 2026-11-30] \
      [--sprint-filter "Sprint 42"] [--window-weeks 12] [--dayfirst] [--map start="Data Início"] \
      [--growth 1.1-1.4] [--forecast-types "História,Bug"] [--backlog-by-type "História=20,Bug=10"] \
      --out-dir out/
  python flow_report.py render --out-dir out/ --narrative out/narrative.json
  python flow_report.py quick --csv export.csv [--as-of 2026-10-07] [--backlog 30] [--target-date 2026-11-30] \
      [--growth 1.2] [--forecast-types "História"] [--backlog-by-type "História=20,Bug=10"] [--dayfirst]
"""
import argparse, json, os, re, sys, base64, io, html, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

CANDIDATES = {
    "id": ["issue key", "chave da item", "chave", "key", "id"],
    "title": ["summary", "resumo", "title", "título", "titulo"],
    "type": ["issue type", "tipo de item", "tipo de ic", "work item type", "tipo de item de trabalho", "tipo"],
    "status": ["status", "state", "estado", "situação"],
    "created": ["created", "criado", "created date", "data de criação", "data de criacao"],
    "start": ["activated date", "data de ativação", "data de ativacao", "start date", "data de início",
              "data de inicio", "in progress date", "started", "status category changed",
              "categoria de status alterada"],
    "done": ["resolved", "resolvido", "closed date", "data de fechamento", "resolved date",
             "data de resolução", "done date", "completed date"],
    "sprint": ["sprint", "iteration path", "caminho da iteração", "caminho da iteracao", "iteration"],
}
CANCEL = re.compile(r"cancel|remov|descart|won.?t|duplic|rejeit", re.I)
DONE_STATUS = re.compile(r"done|closed|conclu|resolv|fechad|finaliz|entregue|pronto", re.I)
# "Resolved" no Azure DevOps costuma ser etapa intermediária: não conta como concluído sem data de conclusão
STRICT_DONE = re.compile(r"^(done|closed|conclu[ií]d[oa]|fechad[oa]|finalizad[oa]|entregue|pronto)$", re.I)
# status de fila (categoria "Itens pendentes"): no Jira, a data de mudança de categoria desses itens não é um início
TODO_STATUS = re.compile(r"^(to ?do|tarefas? pendentes?|itens? pendentes?|a fazer|backlog|aberto|open|novo|new|pendente|selected for development|selecionado para desenvolvimento|pronto para desenvolvimento|ready)$", re.I)

PAL = {"ink": "#1F2937", "muted": "#6B7280", "grid": "#E5E7EB", "blue": "#2563EB",
       "blue_l": "#93C5FD", "amber": "#D97706", "red": "#DC2626", "green": "#059669", "gray": "#9CA3AF"}


# ---------------------------------------------------------------- carga e normalização
def find_col(cols, key, overrides):
    if key in overrides:
        if overrides[key] not in cols:
            sys.exit(f"Coluna mapeada '{overrides[key]}' não existe no CSV. Colunas: {list(cols)}")
        return overrides[key]
    low = {c.lower().strip(): c for c in cols}
    for cand in CANDIDATES[key]:
        if cand in low:
            return low[cand]
    return None


def parse_dates(s, dayfirst):
    s = s.astype(str).str.strip().replace({"": None, "nan": None, "NaT": None, "None": None})
    # Jira pt-BR usa meses abreviados em português
    pt = {"jan": "Jan", "fev": "Feb", "mar": "Mar", "abr": "Apr", "mai": "May", "jun": "Jun", "jul": "Jul",
          "ago": "Aug", "set": "Sep", "out": "Oct", "nov": "Nov", "dez": "Dec"}
    s = s.apply(lambda v: re.sub(r"/(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)/",
                                 lambda m: "/" + pt[m.group(1).lower()] + "/", v, flags=re.I)
                  if isinstance(v, str) else None)
    d = pd.to_datetime(s, errors="coerce", dayfirst=dayfirst, format="mixed")
    try:
        d = d.dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    return d.dt.normalize()


def load(csv, overrides, dayfirst):
    raw = None
    for enc in ("utf-8-sig", "latin-1"):
        for sep in (",", ";"):
            try:
                df = pd.read_csv(csv, encoding=enc, sep=sep, dtype=str)
                if df.shape[1] > 2:
                    raw = df
                    break
            except Exception:
                continue
        if raw is not None:
            break
    if raw is None:
        sys.exit("Não consegui ler o CSV (testei UTF-8/Latin-1 e separador , e ;).")
    cols = list(raw.columns)
    source = "azure_devops" if any(c.lower() in ("work item type", "iteration path", "activated date",
                                                   "tipo de item de trabalho") for c in cols) else "jira"
    m = {k: find_col(cols, k, overrides) for k in CANDIDATES}
    notes = []
    for k in ("id", "status", "done"):
        if m[k] is None:
            sys.exit(f"Coluna obrigatória '{k}' não encontrada. Use --map {k}=\"Nome da coluna\". Colunas: {cols}")
    df = pd.DataFrame({"id": raw[m["id"]].astype(str)})
    df["title"] = raw[m["title"]] if m["title"] else ""
    df["type"] = raw[m["type"]] if m["type"] else "Item"
    df["status"] = raw[m["status"]].fillna("")
    df["created"] = parse_dates(raw[m["created"]], dayfirst) if m["created"] else pd.NaT
    df["done"] = parse_dates(raw[m["done"]], dayfirst)
    start_col = m["start"]
    if start_col:
        df["start"] = parse_dates(raw[start_col], dayfirst)
        if start_col.lower() in ("status category changed", "categoria de status alterada"):
            # esse campo guarda só a ÚLTIMA mudança de categoria: em itens concluídos é a data de conclusão
            is_done = df["done"].notna()
            df.loc[is_done, "start"] = pd.NaT
            # e nos itens ainda na fila ela marca a entrada no backlog, não o início do trabalho
            queued = df["done"].isna() & df["status"].str.strip().str.match(TODO_STATUS)
            df.loc[queued, "start"] = pd.NaT
            notes.append("Início dos itens concluídos não disponível no export (o campo 'Status Category Changed' "
                         "guarda só a última mudança); o Cycle Time usa a data de criação e fica superestimado. "
                         "Recomendação: registrar a data de início num campo próprio.")
    else:
        df["start"] = pd.NaT
        notes.append("Sem coluna de data de início: Cycle Time calculado a partir da criação (na prática, Lead Time).")
    sprint_cols = [c for c in cols if m["sprint"] and (c == m["sprint"] or c.startswith(m["sprint"] + "."))]
    df["sprint"] = raw[sprint_cols].fillna("").astype(str).agg(" | ".join, axis=1) if sprint_cols else ""

    n0 = len(df)
    df = df[~df["status"].str.contains(CANCEL)].copy()
    if n0 - len(df):
        notes.append(f"{n0 - len(df)} item(ns) cancelado(s)/removido(s) excluídos da análise.")
    # item com status de concluído mas sem data de conclusão
    missing_done = df["done"].isna() & df["status"].str.strip().str.match(STRICT_DONE)
    if missing_done.sum():
        notes.append(f"{int(missing_done.sum())} item(ns) com status concluído sem data de conclusão foram ignorados.")
        df = df[~missing_done]
    # data de conclusão preenchida mas item reaberto
    reopened = df["done"].notna() & ~df["status"].str.contains(DONE_STATUS) & (df["status"] != "")
    if reopened.sum():
        notes.append(f"{int(reopened.sum())} item(ns) com data de conclusão mas status aberto (reabertos) "
                     "tratados como em andamento.")
        df.loc[reopened, "done"] = pd.NaT
    fallback = df["start"].isna() & df["done"].notna() & df["created"].notna()
    if fallback.sum() and start_col and not any("superestimado" in n for n in notes):
        notes.append(f"{int(fallback.sum())} item(ns) concluído(s) sem data de início: usada a criação.")
    df["ct_start"] = df["start"].fillna(df["created"])
    no_ref = df["done"].notna() & df["ct_start"].isna()
    if no_ref.sum():
        notes.append(f"{int(no_ref.sum())} item(ns) concluído(s) sem data de início nem de criação ficaram fora do "
                     "Cycle Time (contam só no throughput).")
    bad = df["done"].notna() & df["ct_start"].notna() & (df["done"] < df["ct_start"])
    if bad.sum():
        notes.append(f"{int(bad.sum())} item(ns) com conclusão antes do início foram descartados.")
        df = df[~bad]
    return df, source, m, notes


# ---------------------------------------------------------------- métricas
def analyze(df, s0, s1, window_weeks, backlog, target, sprint_filter, runs=10000, seed=42,
            growth=None, forecast_types=None, backlog_by_type=None):
    s0, s1 = pd.Timestamp(s0), pd.Timestamp(s1)
    w0 = s1 - pd.Timedelta(weeks=window_weeks) + pd.Timedelta(days=1)
    done = df[df["done"].notna()].copy()
    done["ct"] = (done["done"] - done["ct_start"]).dt.days + 1  # convenção inclusiva (Vacanti)
    win = done[(done["done"] >= w0) & (done["done"] <= s1)]
    spr = done[(done["done"] >= s0) & (done["done"] <= s1)]
    sprint_days = (s1 - s0).days + 1
    sprint_weeks = sprint_days / 7

    def pct(x, q):
        return int(np.ceil(np.percentile(x, q))) if len(x) else None

    ct = win["ct"].dropna()
    r = {"window_start": w0.date().isoformat(), "window_weeks": window_weeks, "sprint_days": sprint_days,
         "n_window": int(len(win)), "n_window_ct": int(win["ct"].notna().sum()), "n_sprint": int(len(spr)),
         "ct_p50": pct(ct, 50), "ct_p85": pct(ct, 85), "ct_p95": pct(ct, 95),
         "ct_sprint_p50": pct(spr["ct"].dropna(), 50), "ct_sprint_p85": pct(spr["ct"].dropna(), 85)}

    # throughput semanal (semanas terminando no fim da sprint)
    edges = [s1 - pd.Timedelta(weeks=k) for k in range(window_weeks, -1, -1)]
    wk = []
    for a, b in zip(edges[:-1], edges[1:]):
        wk.append({"week_end": b.date().isoformat(),
                   "count": int(((done["done"] > a) & (done["done"] <= b)).sum())})
    th = np.array([w["count"] for w in wk])
    r["weekly"] = wk
    r["th_week_avg"] = round(float(th.mean()), 1)
    r["th_week_p15"], r["th_week_p85"] = round(float(np.percentile(th, 15)), 1), round(float(np.percentile(th, 85)), 1)
    r["th_sprint_per_week"] = round(len(spr) / sprint_weeks, 1)
    r["by_type_sprint"] = spr["type"].value_counts().to_dict()

    # WIP e Aging no fim da sprint
    wip = df[df["start"].notna() & (df["start"] <= s1) & (df["done"].isna() | (df["done"] > s1))].copy()
    wip["age"] = (s1 - wip["start"]).dt.days + 1
    wip = wip.sort_values("age", ascending=False)
    r["wip_end"] = int(len(wip))
    p85 = r["ct_p85"] or 0
    p50 = r["ct_p50"] or 0
    def zone(a):
        if not p85: return "sem_ref"
        return "critico" if a > p85 else ("atencao" if a > p50 else "ok")
    wip["zone"] = wip["age"].apply(zone)
    r["aging"] = [{"id": i, "title": str(t)[:70], "status": s, "age": int(a), "zone": z}
                  for i, t, s, a, z in wip[["id", "title", "status", "age", "zone"]].itertuples(index=False)]
    r["aging_over_p85"] = int((wip["zone"] == "critico").sum())
    r["no_start_open"] = int((df["done"].isna() & df["start"].isna()).sum())

    # CFD diário (Criado / Iniciado / Concluído) e checagem da Lei de Little
    days = pd.date_range(w0, s1, freq="D")
    arr = np.array([(df["created"] <= d).sum() for d in days])
    stt = np.array([(df["ct_start"].notna() & (df["ct_start"] <= d)).sum() for d in days])
    dep = np.array([(df["done"] <= d).sum() for d in days])
    r["cfd"] = {"days": [d.date().isoformat() for d in days], "created": arr.tolist(),
                "started": stt.tolist(), "done": dep.tolist()}
    wip_daily = stt - dep
    arrivals = int(((df["ct_start"] >= w0) & (df["ct_start"] <= s1)).sum())
    departures = int(len(win))
    avg_wip = float(wip_daily.mean())
    th_day = departures / len(days)
    little_ct = avg_wip / th_day if th_day else None
    obs_ct = float(ct.mean()) if len(ct) else None
    ratio = arrivals / departures if departures else None
    trend = np.polyfit(np.arange(len(wip_daily)), wip_daily, 1)[0] * 7 if len(wip_daily) > 7 else 0
    stable = ratio is not None and 0.8 <= ratio <= 1.2 and abs(trend) < max(1, avg_wip * 0.1)
    r["little"] = {"avg_wip": round(avg_wip, 1), "th_day": round(th_day, 2),
                   "ct_little": round(little_ct, 1) if little_ct else None,
                   "ct_obs_mean": round(obs_ct, 1) if obs_ct else None,
                   "arrivals": arrivals, "departures": departures,
                   "arrival_departure_ratio": round(ratio, 2) if ratio else None,
                   "wip_trend_per_week": round(float(trend), 2), "stable": bool(stable)}

    # Planejado x entregue (se houver campo sprint)
    if sprint_filter:
        mask = df["sprint"].str.contains(re.escape(sprint_filter), case=False)
        plan = df[mask]
        delivered = plan[plan["done"].notna() & (plan["done"] <= s1)]
        r["plan"] = {"planned": int(len(plan)), "delivered": int(len(delivered)),
                     "pct": round(100 * len(delivered) / len(plan)) if len(plan) else None}

    # Métricas por tipo de item: tipos com tamanhos muito diferentes distorcem percentis e previsões
    r["by_type"] = by_type(win, wip, pct)
    big = [t for t in r["by_type"] if t["done"] >= 5 and t["ct_p85"]]
    if len(big) >= 2:
        lo_t, hi_t = min(big, key=lambda t: t["ct_p85"]), max(big, key=lambda t: t["ct_p85"])
        if hi_t["ct_p85"] >= 2 * lo_t["ct_p85"]:
            r["type_spread"] = {"fast": lo_t["type"], "fast_p85": lo_t["ct_p85"],
                                "slow": hi_t["type"], "slow_p85": hi_t["ct_p85"]}

    # Monte Carlo (amostragem do throughput diário da janela, só dias úteis)
    rng = np.random.default_rng(seed)
    types_all = sorted(done["type"].astype(str).unique().tolist() + wip["type"].astype(str).unique().tolist())
    bbt = {resolve_type(k, types_all): v for k, v in (backlog_by_type or {}).items()}
    ftypes = [resolve_type(t, types_all) for t in (forecast_types or [])]
    sample_types = list(bbt) if bbt else ftypes

    def col(types):
        sub = done if not types else done[done["type"].astype(str).isin(types)]
        return daily_counts(sub, w0, s1)

    mc = {"runs": runs, "sample_types": sample_types or None}
    if growth:
        mc["growth"] = {"low": growth[0], "high": growth[1]}
    base = col(sample_types)
    mc["sample_days"] = int(len(base))
    mc["zero_days_pct"] = round(100 * (base == 0).mean()) if len(base) else 100
    if base.sum() == 0:
        mc["error"] = "Nenhuma entrega na janela" + (f" dos tipos {', '.join(sample_types)}" if sample_types else "") \
                      + ": sem base para simular."
    else:
        if bbt or backlog:
            if bbt:
                sample = np.column_stack([col([t]) for t in bbt])
                targets = list(bbt.values())
            else:
                sample, targets = base[:, None], [backlog]
            res = mc_when(sample, targets, runs, rng, growth)
            if "error" in res:
                mc["error_when"] = res["error"]
            else:
                sims = res["days"]
                dates = {str(q): (s1 + pd.offsets.BDay(int(np.percentile(sims, q)))).date().isoformat()
                         for q in (50, 85, 95)}
                mc["when"] = {"backlog": int(sum(targets)), "dates": dates, "hist": np.bincount(sims).tolist(),
                              "capped_runs": res["capped"]}
                if bbt:
                    mc["when"]["by_type"] = bbt
        if target:
            t = pd.Timestamp(target)
            n = len(pd.bdate_range(s1 + pd.Timedelta(days=1), t))
            tot = base[rng.integers(len(base), size=(runs, n))].sum(axis=1) if n else np.zeros(runs, int)
            hm = {"target": t.date().isoformat(), "business_days": n}
            if growth:
                # parte da vazão futura vai para itens que ainda vão surgir (divisões, descobertas):
                # sobra para o escopo de hoje a vazão total dividida pelo fator de crescimento
                f = rng.uniform(growth[0], growth[1], size=runs) if growth[1] > growth[0] else growth[0]
                hm["items_total"] = {"50": int(np.percentile(tot, 50)), "85": int(np.percentile(tot, 15)),
                                     "95": int(np.percentile(tot, 5))}
                tot = np.floor(tot / f)
            hm["items"] = {"50": int(np.percentile(tot, 50)), "85": int(np.percentile(tot, 15)),
                           "95": int(np.percentile(tot, 5))}
            mc["how_many"] = hm
    r["mc"] = mc
    return r, done, win, spr, wip


def by_type(win, wip, pct):
    out = []
    types = sorted(set(win["type"].astype(str)) | set(wip["type"].astype(str)))
    for t in types:
        g = win[win["type"].astype(str) == t]
        c = g["ct"].dropna()
        out.append({"type": t, "done": int(len(g)),
                    "share_pct": round(100 * len(g) / len(win)) if len(win) else 0,
                    "ct_p50": pct(c, 50), "ct_p85": pct(c, 85), "ct_p95": pct(c, 95),
                    "wip": int((wip["type"].astype(str) == t).sum())})
    return sorted(out, key=lambda x: -x["done"])


def resolve_type(name, types):
    low = {t.lower().strip(): t for t in types}
    key = name.lower().strip()
    if key not in low:
        sys.exit(f"Tipo de item '{name}' não existe no CSV. Tipos encontrados: {sorted(set(types))}")
    return low[key]


def daily_counts(done, w0, s1):
    """Throughput por dia útil na janela, com os dias zerados. Entregas no fim de semana contam no dia útil anterior."""
    bdays = pd.bdate_range(w0, s1)
    d = done.loc[(done["done"] >= w0) & (done["done"] <= s1), "done"]
    d = d.apply(lambda x: (x - pd.offsets.BDay(1)).normalize() if x.weekday() >= 5 else x)
    counts = d.value_counts()
    return np.array([int(counts.get(b, 0)) for b in bdays])


def mc_when(sample, targets, runs, rng, growth):
    """Dias úteis até concluir os alvos. sample: dias × tipos. Sorteia o mesmo dia para todos os tipos,
    preservando a capacidade compartilhada (um dia bom para bugs costuma ser um dia fraco para histórias)."""
    tg = np.asarray(targets, dtype=float)
    if np.any((tg > 0) & (sample.sum(axis=0) == 0)):
        return {"error": "Algum tipo pedido não teve nenhuma entrega na janela: sem base para prever quando termina."}
    lo, hi = growth or (1.0, 1.0)
    nd, single = len(sample), sample.shape[1] == 1
    col0 = sample[:, 0]
    sims = np.empty(runs, dtype=int); capped = 0
    for i in range(runs):
        f = rng.uniform(lo, hi) if hi > lo else lo
        need = np.ceil(tg * f)
        n = 0
        if single:
            total, goal = 0, need[0]
            while total < goal:
                total += col0[rng.integers(nd)]; n += 1
                if n > 2000: capped += 1; break
        else:
            cum = np.zeros_like(tg)
            while np.any(cum < need):
                cum += sample[rng.integers(nd)]; n += 1
                if n > 2000: capped += 1; break
        sims[i] = n
    return {"days": sims, "capped": capped}


# ---------------------------------------------------------------- gráficos
def style(ax):
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color(PAL["grid"])
    ax.tick_params(colors=PAL["muted"], labelsize=7.5, length=0)
    ax.grid(axis="y", color=PAL["grid"], lw=0.6); ax.set_axisbelow(True)


def to_b64(fig):
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=200, bbox_inches="tight"); plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def charts(r, win, spr, s0, s1):
    out = {}
    s0, s1 = pd.Timestamp(s0), pd.Timestamp(s1)
    fs = (4.6, 2.3)
    # 1. Scatterplot de Cycle Time
    fig, ax = plt.subplots(figsize=fs); style(ax)
    old = win[win["done"] < s0]
    ax.scatter(old["done"], old["ct"], s=12, color=PAL["gray"], alpha=.6, lw=0, label="Janela")
    ax.scatter(spr["done"], spr["ct"], s=16, color=PAL["blue"], lw=0, label="Sprint")
    for q, c, ls in (("ct_p50", PAL["muted"], ":"), ("ct_p85", PAL["amber"], "--"), ("ct_p95", PAL["red"], "--")):
        if r[q]:
            ax.axhline(r[q], color=c, ls=ls, lw=.9)
            ax.text(win["done"].max() if len(win) else s1, r[q], f"P{q[-2:]} {r[q]}d",
                    color=c, fontsize=7, va="bottom", ha="right", bbox=dict(fc="white", ec="none", pad=1))
    ax.axvspan(s0, s1, color=PAL["blue"], alpha=.05, lw=0)
    ax.set_ylabel("dias", fontsize=7.5, color=PAL["muted"])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.legend(fontsize=7, frameon=False, loc="upper left")
    out["scatter"] = to_b64(fig)
    # 2. Throughput semanal
    fig, ax = plt.subplots(figsize=fs); style(ax)
    wk = r["weekly"]; x = np.arange(len(wk)); y = [w["count"] for w in wk]
    cols = [PAL["blue"] if pd.Timestamp(w["week_end"]) > s0 else PAL["blue_l"] for w in wk]
    ax.bar(x, y, color=cols, width=.7)
    ax.axhline(r["th_week_avg"], color=PAL["muted"], ls=":", lw=.9)
    ax.text(-.4, r["th_week_avg"], f"média {str(r['th_week_avg']).replace('.', ',')}", fontsize=7, color=PAL["muted"],
            ha="left", va="bottom", bbox=dict(fc="white", ec="none", pad=1))
    ax.set_xticks(x); ax.set_xticklabels([pd.Timestamp(w["week_end"]).strftime("%d/%m") for w in wk], fontsize=6.5)
    ax.set_ylabel("itens/semana", fontsize=7.5, color=PAL["muted"])
    out["throughput"] = to_b64(fig)
    # 3. CFD
    fig, ax = plt.subplots(figsize=fs); style(ax)
    c = r["cfd"]; d = pd.to_datetime(c["days"])
    base = min(c["done"])
    dn = np.array(c["done"]) - base; st = np.array(c["started"]) - base; cr = np.array(c["created"]) - base
    ax.fill_between(d, 0, dn, color=PAL["green"], alpha=.75, lw=0, label="Concluído")
    ax.fill_between(d, dn, st, color=PAL["blue"], alpha=.7, lw=0, label="Em andamento")
    ax.fill_between(d, st, cr, color=PAL["gray"], alpha=.45, lw=0, label="Backlog (criado)")
    ax.axvspan(s0, s1, color=PAL["ink"], alpha=.04, lw=0)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.set_ylabel("itens (acumulado)", fontsize=7.5, color=PAL["muted"])
    ax.legend(fontsize=7, frameon=False, loc="upper left")
    out["cfd"] = to_b64(fig)
    # 4. Aging WIP
    ag = r["aging"][:12]
    fig, ax = plt.subplots(figsize=fs); style(ax); ax.grid(axis="y", visible=False); ax.grid(axis="x", color=PAL["grid"], lw=.6)
    if ag:
        cmap = {"critico": PAL["red"], "atencao": PAL["amber"], "ok": PAL["green"], "sem_ref": PAL["gray"]}
        yy = np.arange(len(ag))[::-1]
        ax.barh(yy, [a["age"] for a in ag], color=[cmap[a["zone"]] for a in ag], height=.65)
        ax.set_yticks(yy); ax.set_yticklabels([f'{a["id"]} · {a["status"][:14]}' for a in ag], fontsize=6.5)
        for q, col in (("ct_p50", PAL["muted"]), ("ct_p85", PAL["amber"])):
            if r[q]: ax.axvline(r[q], color=col, ls="--", lw=.9); ax.text(r[q], len(ag) - .4, f"P{q[-2:]}", fontsize=6.5, color=col)
        ax.set_xlabel("idade (dias)", fontsize=7.5, color=PAL["muted"])
    else:
        ax.text(.5, .5, "Sem itens em andamento", ha="center", va="center", transform=ax.transAxes, color=PAL["muted"])
        ax.set_xticks([]); ax.set_yticks([])
    out["aging"] = to_b64(fig)
    return out


# ---------------------------------------------------------------- HTML
def nb(x):
    return str(x).replace('.', ',')


def br(d):
    return pd.Timestamp(d).strftime("%d/%m/%Y") if d else "—"


def render(out_dir, narrative_path):
    M = json.load(open(os.path.join(out_dir, "metrics.json"), encoding="utf-8"))
    C = json.load(open(os.path.join(out_dir, "charts.json"), encoding="utf-8"))
    N = json.load(open(narrative_path, encoding="utf-8")) if narrative_path else {}
    e = html.escape
    status = N.get("status", "").lower()
    rag = {"verde": ("#059669", "No trilho"), "amarelo": ("#D97706", "Atenção"), "vermelho": ("#DC2626", "Em risco")}
    rag_c, rag_t = rag.get(status, ("#6B7280", "Status"))

    def lst(key, empty=None):
        items = N.get(key) or []
        lis = "".join(f"<li>{e(i)}</li>" for i in items) or (f"<li>{e(empty)}</li>" if empty else "<li></li>")
        return f"<ul class='ed' contenteditable='true'>{lis}</ul>"

    def kpi(label, value, sub, tone=""):
        return f"<div class='kpi {tone}'><div class='kl'>{e(label)}</div><div class='kv'>{value}</div><div class='ks'>{sub}</div></div>"

    th_delta = M["th_sprint_per_week"] - M["th_week_avg"]
    th_tone = "good" if th_delta >= 0 else ("warn" if M["th_sprint_per_week"] >= M["th_week_p15"] else "bad")
    kpis = [kpi("Entregues na sprint", M["n_sprint"], f"{nb(M['th_sprint_per_week'])}/sem · média {nb(M['th_week_avg'])}/sem", th_tone)]
    if M.get("plan"):
        p = M["plan"]; tone = "good" if (p["pct"] or 0) >= 80 else ("warn" if (p["pct"] or 0) >= 60 else "bad")
        kpis.append(kpi("Planejado × entregue", f"{p['pct']}%", f"{p['delivered']} de {p['planned']} itens", tone))
    kpis.append(kpi("Cycle Time P85", f"{M['ct_p85']}d", f"85% dos itens em até {M['ct_p85']} dias · P50 {M['ct_p50']}d")
                if M["ct_p85"] else kpi("Cycle Time P85", "—", "sem itens concluídos com data de início na janela"))
    kpis.append(kpi("WIP atual", M["wip_end"], f"média na janela {nb(M['little']['avg_wip'])}"))
    ag_tone = "bad" if M["aging_over_p85"] else "good"
    kpis.append(kpi("Itens acima do P85", M["aging_over_p85"], "envelhecendo além do padrão histórico", ag_tone))

    mc = M["mc"]; fc = ""
    if mc.get("when"):
        w = mc["when"]; dd = w["dates"]
        what = (" + ".join(f"{v} {e(k)}" for k, v in w["by_type"].items()) if w.get("by_type")
                else f"{w['backlog']} itens" + (f" ({e(', '.join(mc['sample_types']))})" if mc.get("sample_types") else ""))
        fc += (f"<div class='fc'><div class='fct'>Quando terminamos {what}?</div>"
               f"<div class='fcr'><span><b>{br(dd['50'])}</b><i>50%</i></span><span class='hl'><b>{br(dd['85'])}</b><i>85%</i></span>"
               f"<span><b>{br(dd['95'])}</b><i>95%</i></span></div></div>")
    if mc.get("how_many"):
        h = mc["how_many"]; it = h["items"]
        tipos = f" ({e(', '.join(mc['sample_types']))})" if mc.get("sample_types") else ""
        fc += (f"<div class='fc'><div class='fct'>Quantos itens{tipos} até {br(h['target'])}?</div>"
               f"<div class='fcr'><span><b>{it['95']}+</b><i>95%</i></span><span class='hl'><b>{it['85']}+</b><i>85%</i></span>"
               f"<span><b>{it['50']}+</b><i>50%</i></span></div></div>")
    if fc:
        extra = ""
        if mc.get("when", {}).get("by_type"):
            extra += f"Vazão de cada tipo ({e(', '.join(mc['sample_types']))}) simulada em conjunto, sorteando os mesmos dias. "
        elif mc.get("sample_types"):
            extra += f"Throughput só dos tipos {e(', '.join(mc['sample_types']))}. "
        if mc.get("growth"):
            g = mc["growth"]
            faixa = (f"+{round((g['low'] - 1) * 100)}%" if g["low"] == g["high"]
                     else f"+{round((g['low'] - 1) * 100)}% a +{round((g['high'] - 1) * 100)}%")
            extra += f"Considera crescimento de escopo de {faixa} (itens que se dividem ou são descobertos no caminho). "
        fc = (f"<section class='card'><h3>Previsão (Monte Carlo)</h3>{fc}<p class='foot'>{mc['runs']:,} simulações".replace(",", ".")
              + f" com o throughput diário das últimas {M['window_weeks']} semanas ({mc['sample_days']} dias úteis). "
              + extra +
              "Probabilidade de concluir até a data indicada, assumindo que o sistema continue se comportando como no período.</p></section>")

    reading = lst("reading", "Sem leitura registrada.")
    quality = M.get("quality_notes", [])
    ql = "".join(f"<li>{e(q)}</li>" for q in quality)
    lt = M["little"]
    little_txt = (f"Lei de Little: WIP médio {nb(lt['avg_wip'])} ÷ throughput {nb(lt['th_day'])}/dia ≈ {nb(lt['ct_little'])}d "
                  f"(média observada {nb(lt['ct_obs_mean'])}d). Entradas/saídas na janela: {lt['arrivals']}/{lt['departures']}. "
                  + ("Sistema aproximadamente estável." if lt["stable"] else
                     "Sistema instável no período (entradas e saídas descompassadas ou WIP em tendência): use os números com cautela."))

    css = """
    @page{size:A4 landscape;margin:9mm}
    *{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}body{font-family:Inter,'Segoe UI',Roboto,Arial,sans-serif;color:#1F2937;margin:0;background:#F3F4F6;font-size:11.5px;line-height:1.4}
    .page{max-width:1180px;margin:16px auto;background:#fff;padding:22px 26px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
    header{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid #1F2937;padding-bottom:10px;margin-bottom:12px;gap:16px}
    h1{font-size:20px;margin:0}h1 small{font-weight:500;color:#6B7280;font-size:14px}.meta{color:#6B7280;margin-top:3px}
    .rag{display:flex;align-items:center;gap:8px;font-weight:600;white-space:nowrap}.dot{display:inline-block;width:0;height:0;border:7px solid #6B7280;border-radius:50%;-webkit-print-color-adjust:exact;print-color-adjust:exact}
    .goal{background:#F9FAFB;border-left:3px solid #2563EB;padding:7px 11px;margin-bottom:12px}.goal b{color:#2563EB}
    .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:12px}
    .kpi{border:1px solid #E5E7EB;border-radius:8px;padding:8px 11px;border-top:3px solid #9CA3AF}
    .kpi.good{border-top-color:#059669}.kpi.warn{border-top-color:#D97706}.kpi.bad{border-top-color:#DC2626}
    .kl{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:#6B7280}.kv{font-size:22px;font-weight:700;margin:2px 0}.ks{font-size:10px;color:#6B7280}
    .grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}.span2{grid-column:span 2}
    .card{border:1px solid #E5E7EB;border-radius:8px;padding:9px 12px}.card h3{margin:0 0 5px;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:#374151}
    .card img{width:100%;display:block}.charts{display:grid;grid-template-columns:1fr 1fr;gap:10px}
    ul{margin:0;padding-left:16px}li{margin:2px 0}.muted{color:#9CA3AF;margin:0}
    .fc{margin:4px 0 6px}.fct{font-weight:600;margin-bottom:4px}.fcr{display:flex;gap:6px}
    .fcr span{flex:1;background:#F9FAFB;border-radius:6px;padding:5px;text-align:center}.fcr span.hl{background:#EFF6FF;outline:1px solid #93C5FD}
    .fcr b{display:block;font-size:13px}.fcr i{font-style:normal;font-size:10px;color:#6B7280}
    .foot{font-size:9.5px;color:#6B7280;margin:6px 0 0}
    .notes{margin-top:10px;font-size:9.5px;color:#6B7280;border-top:1px solid #E5E7EB;padding-top:6px}.notes ul{padding-left:14px}
    .tb{position:sticky;top:0;z-index:5;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;background:#1F2937;color:#F9FAFB;padding:8px 16px;font-size:12px}
    .tb button{font:inherit;border:1px solid #6B7280;background:transparent;color:#F9FAFB;border-radius:6px;padding:5px 10px;margin-left:6px;cursor:pointer}
    .tb button.pri{background:#2563EB;border-color:#2563EB}
    .ed{outline:none;border-radius:4px;transition:background .15s}.ed:hover{background:#FEF9C3}.ed:focus{background:#FEF3C7;box-shadow:0 0 0 2px #FCD34D}
    span.ed:empty::before,b.ed:empty::before{content:"(clique para preencher)";color:#9CA3AF;font-weight:400}
    ul.ed li:empty::before{content:"(clique para adicionar)";color:#9CA3AF}
    #rag{cursor:pointer}
    @media print{.noprint{display:none!important}.ed:hover,.ed:focus{background:none;box-shadow:none}span.ed:empty::before,b.ed:empty::before{content:""}ul.ed li:empty{display:none}.goal:has(span.ed:empty){display:none}}
    @media print{html,body{background:#fff!important;width:auto}.page{box-shadow:none;margin:0;padding:0;border-radius:0;width:1180px!important;max-width:none!important;zoom:var(--pz,.7)}.grid{grid-template-columns:1fr 1fr 1fr!important}.span2{grid-column:span 2!important}.charts{grid-template-columns:1fr 1fr!important}header{flex-direction:row!important}.kpis{grid-template-columns:repeat(var(--n,5),1fr)!important}.card,.kpi{break-inside:avoid}}
    html.pm .grid{grid-template-columns:1fr 1fr 1fr!important} html.pm .span2{grid-column:span 2!important} html.pm .charts{grid-template-columns:1fr 1fr!important} html.pm header{flex-direction:row!important} html.pm .kpis{grid-template-columns:repeat(var(--n,5),1fr)!important} html.pm .page{width:1180px!important;max-width:none!important;padding:0!important}
    @media screen and (max-width:800px){.grid,.charts{grid-template-columns:1fr}.span2{grid-column:auto}header{flex-direction:column}}
    """
    body = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>One Page Report · {e(M['sprint_name'])}</title><style>{css}</style></head><body><div class="tb noprint"><span>✏️ <b>Modo edição:</b> clique em qualquer texto para alterar · Enter cria novo tópico · clique na bolinha do status para trocar a cor · ao imprimir: Destino “Salvar como PDF”, Layout Paisagem, Margens Padrão</span>
<span><button onclick="salvar()">Salvar versão editada</button><button class="pri" onclick="imprimir()">Imprimir / PDF</button></span></div>
<div class="page">
<header><div><h1>{e(M['team'])} <small>· {e(M['sprint_name'])}</small></h1>
<div class="meta">{br(M['sprint_start'])} a {br(M['sprint_end'])} · {M['sprint_days']} dias · Report: <span class="ed" contenteditable="true">{e(N.get('author',''))}</span></div></div>
<div class="rag"><span class="dot" id="rag" data-c="{rag_c}" title="Clique para trocar o status" style="border-color:{rag_c}"></span><span class="ed" contenteditable="true">{e(N.get('status_label', rag_t))}</span></div></header>
<div class="goal"><b>Objetivo da sprint:</b> <span class="ed" contenteditable="true">{e(N.get('sprint_goal', ''))}</span> — <b class="ed" contenteditable="true">{e(N.get('goal_result', ''))}</b></div>
<div class="kpis" style="--n:{len(kpis)}">{''.join(kpis)}</div>
<div class="grid">
 <section class="card span2"><h3>Leitura dos dados</h3>{reading}</section>
 <section class="card"><h3>Destaques</h3>{lst('highlights')}</section>
 <section class="card span2"><div class="charts">
   <div><h3>Cycle Time por item</h3><img src="data:image/png;base64,{C['scatter']}" alt="Scatterplot de Cycle Time"></div>
   <div><h3>Throughput semanal</h3><img src="data:image/png;base64,{C['throughput']}" alt="Throughput semanal"></div>
   <div><h3>Fluxo acumulado (CFD)</h3><img src="data:image/png;base64,{C['cfd']}" alt="CFD"></div>
   <div><h3>Aging WIP</h3><img src="data:image/png;base64,{C['aging']}" alt="Aging WIP"></div>
 </div></section>
 <div style="display:flex;flex-direction:column;gap:12px">
  {fc}
  <section class="card"><h3>Riscos e impedimentos</h3>{lst('risks')}</section>
  <section class="card"><h3>Decisões necessárias</h3>{lst('decisions')}</section>
  <section class="card"><h3>Próxima sprint</h3>{lst('next')}</section>
 </div>
</div>
<div class="notes"><b>Método:</b> Cycle Time = conclusão − início + 1 dia; percentis sobre {M.get('n_window_ct', M['n_window'])} itens concluídos com data de início ou criação nas últimas {M['window_weeks']} semanas.
Métricas do trabalho, não de pessoas. {e(little_txt)}{('<ul>' + ql + '</ul>') if ql else ''}</div>
</div><script>
const cores=["#059669","#D97706","#DC2626","#6B7280"];
document.getElementById("rag").onclick=e=>{{const d=e.target,i=cores.indexOf(d.dataset.c||"");const n=cores[(i+1)%cores.length];d.style.borderColor=n;d.dataset.c=n;}};
document.querySelectorAll("ul.ed").forEach(u=>u.addEventListener("input",()=>{{if(!u.querySelector("li")){{u.innerHTML="<li></li>";}}}}));
function ajustarImpressao(){{const r=document.documentElement,pg=document.querySelector(".page");r.classList.add("pm");const h=pg.scrollHeight;r.classList.remove("pm");const z=Math.min(1050/1180,715/h)*0.98;document.documentElement.style.setProperty("--pz",z.toFixed(3));}}
window.addEventListener("beforeprint",ajustarImpressao);
function imprimir(){{ajustarImpressao();window.print();}}
function salvar(){{const h="<!doctype html>"+document.documentElement.outerHTML;const a=document.createElement("a");
a.href=URL.createObjectURL(new Blob([h],{{type:"text/html"}}));a.download=(location.pathname.split("/").pop()||"one-page-report.html").replace(/(-editado)?[.]html$/,"-editado.html");a.click();}}
</script></body></html>"""
    path = os.path.join(out_dir, N.get("filename", f"one-page-report-{re.sub(r'[^a-z0-9]+', '-', M['sprint_name'].lower())}.html"))
    open(path, "w", encoding="utf-8").write(body)
    print(path)


# ---------------------------------------------------------------- CLI
def parse_growth(v):
    """'1.2' → (1.2, 1.2); '1.1-1.4' → (1.1, 1.4). Aceita vírgula decimal."""
    if not v:
        return None
    parts = [float(p.replace(",", ".")) for p in re.split(r"\s*[-–:]\s*", v.strip()) if p]
    lo, hi = (parts[0], parts[0]) if len(parts) == 1 else (min(parts[:2]), max(parts[:2]))
    if lo < 1 or hi > 5:
        sys.exit("--growth é um fator multiplicador do escopo: use valores entre 1.0 e 5.0 (ex.: 1.2 ou 1.1-1.4).")
    return (lo, hi)


def parse_types(v):
    return [t.strip() for t in v.split(",") if t.strip()] if v else None


def parse_backlog_by_type(v):
    if not v:
        return None
    out = {}
    for part in v.split(","):
        if "=" not in part:
            sys.exit('--backlog-by-type usa o formato "Tipo=quantidade,Tipo=quantidade".')
        k, n = part.rsplit("=", 1)
        out[k.strip()] = int(n)
    return out


def fit_window(df, s1, window_weeks, notes):
    # export mais curto que a janela: semanas sem nenhum dado entrariam como zero e puxariam a média para baixo
    first = pd.concat([df["ct_start"], df["done"]]).dropna().min()
    if pd.notna(first):
        covered = max(1, ((s1 - first).days + 1) // 7)
        if covered < window_weeks:
            notes.append(f"O export só tem dados a partir de {first:%d/%m/%Y}: a janela de histórico foi reduzida "
                         f"de {window_weeks} para {covered} semanas.")
            return covered
    return window_weeks


def common_notes(r, notes):
    if r["n_window"] < 20:
        notes.append(f"Amostra pequena ({r['n_window']} itens na janela): percentis e previsões têm baixa confiabilidade. "
                     "Use-os como referência inicial, não como padrão.")
    if r["mc"].get("zero_days_pct", 0) > 60:
        notes.append(f"{r['mc']['zero_days_pct']}% dos dias úteis sem entrega: entregas em lote reduzem a precisão do Monte Carlo.")
    if r["mc"].get("when", {}).get("capped_runs"):
        notes.append(f"{r['mc']['when']['capped_runs']} simulações passaram de 2.000 dias úteis e foram interrompidas: "
                     "a vazão do histórico é baixa demais para esse volume; a previsão 'quando' é otimista.")
    if r["no_start_open"]:
        notes.append(f"{r['no_start_open']} item(ns) abertos sem data de início (considerados backlog, fora do WIP).")
    ts = r.get("type_spread")
    if ts and not r["mc"].get("sample_types"):
        notes.append(f"O Cycle Time varia muito entre tipos de item ({ts['fast']}: 85% em até {ts['fast_p85']}d; "
                     f"{ts['slow']}: 85% em até {ts['slow_p85']}d). Para previsões, separe por tipo "
                     "(--forecast-types ou --backlog-by-type).")


def add_forecast_args(p):
    p.add_argument("--backlog", type=int, help="itens restantes (responde 'quando terminamos?')")
    p.add_argument("--target-date", help="data-alvo (responde 'quantos itens até lá?')")
    p.add_argument("--growth", help="fator de crescimento de escopo: 1.2 ou faixa 1.1-1.4")
    p.add_argument("--forecast-types", help='tipos usados na previsão, ex.: "História,Bug"')
    p.add_argument("--backlog-by-type", help='itens restantes por tipo, ex.: "História=20,Bug=10" (substitui --backlog)')
    p.add_argument("--window-weeks", type=int, default=12); p.add_argument("--dayfirst", action="store_true")
    p.add_argument("--map", action="append", default=[], help='campo="Nome da coluna"')


def quick(args):
    """Análise rápida, sem sprint e sem report: responde uma pergunta de fluxo com os números do CSV."""
    overrides = dict(m.split("=", 1) for m in args.map)
    df, source, mapping, notes = load(args.csv, overrides, args.dayfirst)
    latest = pd.concat([df["done"], df["start"], df["created"]]).dropna().max()
    if args.as_of:
        s1 = pd.Timestamp(args.as_of)
    else:
        s1 = pd.Timestamp.today().normalize()
        if pd.notna(latest) and (s1 - latest).days > 7:
            notes.append(f"A última movimentação do export é de {latest:%d/%m/%Y}: a análise usa essa data como 'hoje'. "
                         "Se o export é antigo, a idade dos itens em andamento está subestimada; use --as-of para ajustar.")
            s1 = latest
    window = fit_window(df, s1, args.window_weeks, notes)
    r, done, win, spr, wip = analyze(df, s1 - pd.Timedelta(days=6), s1, window, args.backlog, args.target_date, None,
                                     growth=parse_growth(args.growth), forecast_types=parse_types(args.forecast_types),
                                     backlog_by_type=parse_backlog_by_type(args.backlog_by_type))
    common_notes(r, notes)
    th = np.array([w["count"] for w in r["weekly"]])
    out = {"as_of": s1.date().isoformat(), "window_start": r["window_start"], "window_weeks": r["window_weeks"],
           "source": source, "column_mapping": mapping,
           "n_window": r["n_window"], "n_window_ct": r["n_window_ct"],
           "ct_p50": r["ct_p50"], "ct_p85": r["ct_p85"], "ct_p95": r["ct_p95"],
           "delivered_last_7d": r["n_sprint"],
           "throughput_week": {"avg": r["th_week_avg"], "median": float(np.median(th)) if len(th) else None,
                               "p15": r["th_week_p15"], "p85": r["th_week_p85"],
                               "last_4_weeks": [w["count"] for w in r["weekly"][-4:]]},
           "weekly": r["weekly"], "wip": r["wip_end"], "aging_over_p85": r["aging_over_p85"], "aging": r["aging"],
           "little": r["little"], "by_type": r["by_type"], "mc": {k: v for k, v in r["mc"].items()},
           "quality_notes": notes}
    if "when" in out["mc"]:
        out["mc"]["when"] = {k: v for k, v in out["mc"]["when"].items() if k != "hist"}
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        json.dump(out, open(os.path.join(args.out_dir, "quick.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1, default=str)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("--csv", required=True); a.add_argument("--sprint-start", required=True)
    a.add_argument("--sprint-end", required=True); a.add_argument("--sprint-name", default="Sprint")
    a.add_argument("--team", default="Time"); a.add_argument("--sprint-filter")
    add_forecast_args(a)
    a.add_argument("--out-dir", default="out")
    q = sub.add_parser("quick", help="análise rápida sem sprint e sem report")
    q.add_argument("--csv", required=True); q.add_argument("--as-of", help="data de referência (padrão: hoje)")
    add_forecast_args(q)
    q.add_argument("--out-dir")
    rp = sub.add_parser("render"); rp.add_argument("--out-dir", default="out"); rp.add_argument("--narrative")
    args = ap.parse_args()
    if args.cmd == "render":
        return render(args.out_dir, args.narrative)
    if args.cmd == "quick":
        return quick(args)
    os.makedirs(args.out_dir, exist_ok=True)
    overrides = dict(m.split("=", 1) for m in args.map)
    df, source, mapping, notes = load(args.csv, overrides, args.dayfirst)
    args.window_weeks = fit_window(df, pd.Timestamp(args.sprint_end), args.window_weeks, notes)
    r, done, win, spr, wip = analyze(df, args.sprint_start, args.sprint_end, args.window_weeks,
                                     args.backlog, args.target_date, args.sprint_filter,
                                     growth=parse_growth(args.growth), forecast_types=parse_types(args.forecast_types),
                                     backlog_by_type=parse_backlog_by_type(args.backlog_by_type))
    common_notes(r, notes)
    r.update({"source": source, "column_mapping": mapping, "quality_notes": notes, "team": args.team,
              "sprint_name": args.sprint_name, "sprint_start": args.sprint_start, "sprint_end": args.sprint_end,
              "sprint_items": [{**i, "ct": None if pd.isna(i["ct"]) else int(i["ct"])}
                               for i in spr[["id", "title", "type", "ct"]].to_dict("records")]})
    ch = charts(r, win, spr, args.sprint_start, args.sprint_end)
    json.dump(ch, open(os.path.join(args.out_dir, "charts.json"), "w"))
    json.dump(r, open(os.path.join(args.out_dir, "metrics.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    slim = {k: v for k, v in r.items() if k not in ("cfd", "weekly", "sprint_items")}
    slim["aging"] = r["aging"][:10]
    if "when" in slim.get("mc", {}): slim["mc"] = {**slim["mc"], "when": {k: v for k, v in slim["mc"]["when"].items() if k != "hist"}}
    print(json.dumps(slim, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
