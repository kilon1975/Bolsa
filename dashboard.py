"""Genera dashboard.html estático con estado de ambos sleeves."""
import json
import os
import glob
from datetime import datetime, timezone

from go_live_gate import evaluate as gate_eval


def _load(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _tail_log(pattern, n=50):
    files = sorted(glob.glob(pattern))
    if not files:
        return []
    lines = []
    with open(files[-1]) as f:
        for line in f:
            try:
                lines.append(json.loads(line))
            except Exception:
                pass
    return lines[-n:]


def _fmt_money(v):
    try: return f"${float(v):,.2f}"
    except: return str(v)


def _row(cells, header=False):
    tag = "th" if header else "td"
    return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"


def render_sleeve(name, state_path, log_pattern):
    state = _load(state_path)
    gate = gate_eval(state_path)
    positions = state.get("positions", {})
    history = state.get("trade_history", [])
    recent_logs = _tail_log(log_pattern, 30)

    pos_rows = [_row(["Símbolo", "Qty", "Entry", "Max/Stop", "Abierta"], header=True)]
    for sym, p in positions.items():
        mx = p.get("max_price") or p.get("entry_price") or ""
        stop = p.get("stop_price", "")
        pos_rows.append(_row([sym, p.get("quantity", ""),
                              _fmt_money(p.get("entry_price", 0)),
                              f"{_fmt_money(mx)} / {_fmt_money(stop)}" if stop else _fmt_money(mx),
                              (p.get("opened_at") or p.get("updated_at") or "")[:19]]))
    pos_table = "<table>" + "\n".join(pos_rows) + "</table>" if len(pos_rows) > 1 else "<p><em>Sin posiciones abiertas</em></p>"

    hist_rows = [_row(["Símbolo", "Qty", "Entry", "Exit", "PnL", "Razón", "Cerrada"], header=True)]
    for t in history[-20:][::-1]:
        pnl = t.get("pnl")
        pnl_cls = ""
        if pnl is not None:
            pnl_cls = "pos" if pnl >= 0 else "neg"
        hist_rows.append(_row([t.get("symbol", ""), t.get("quantity", ""),
                               _fmt_money(t.get("entry_price", 0)),
                               _fmt_money(t.get("exit_price", 0)),
                               f"<span class='{pnl_cls}'>{_fmt_money(pnl or 0)}</span>",
                               t.get("reason", ""),
                               (t.get("closed_at") or "")[:19]]))
    hist_table = "<table>" + "\n".join(hist_rows) + "</table>" if history else "<p><em>Sin trades cerrados</em></p>"

    gate_rows = [_row(["Check", "OK", "Detalle"], header=True)]
    for name_, ok, detail in gate.checks:
        mark = "✅" if ok else "❌"
        gate_rows.append(_row([name_, mark, detail]))
    gate_table = "<table>" + "\n".join(gate_rows) + "</table>"
    gate_banner = "✅ APTO PARA LIVE" if gate.passes else "❌ Aún no apto para LIVE"

    red_flags = []
    for r in recent_logs:
        if r.get("event") == "screen" and r.get("news_red"):
            red_flags.append(f"{r['symbol']}: {', '.join(r['news_red'])}")
    red_html = "<ul>" + "".join(f"<li>🚩 {r}</li>" for r in red_flags) + "</ul>" if red_flags else "<p><em>Sin red flags</em></p>"

    # Solo mostrar análisis Buffett al sleeve LATAM (donde aplica value investing)
    buffett_html = _buffett_section(positions) if "LATAM" in name else ""

    m = gate.metrics
    return f"""
    <section>
      <h2>{name}</h2>
      <div class="metrics">
        <div><span>Trades</span><strong>{m.get('n_trades', 0)}</strong></div>
        <div><span>Win rate</span><strong>{m.get('win_rate', 0)*100:.1f}%</strong></div>
        <div><span>Payoff</span><strong>{m.get('payoff_ratio', 0):.2f}</strong></div>
        <div><span>PnL total</span><strong>{_fmt_money(m.get('total_pnl', 0))}</strong></div>
        <div><span>Equity peak</span><strong>{_fmt_money(m.get('equity_peak', 0))}</strong></div>
        <div><span>DD actual</span><strong>{m.get('current_drawdown', 0)*100:.1f}%</strong></div>
        <div><span>Días activos</span><strong>{m.get('days_active', 0)}</strong></div>
      </div>

      <h3>Gate LIVE — <small>{gate_banner}</small></h3>
      {gate_table}

      <h3>Posiciones abiertas</h3>
      {pos_table}

      <h3>Historial reciente (últimos 20)</h3>
      {hist_table}

      <h3>Red flags de noticias</h3>
      {red_html}
      {buffett_html}
    </section>
    """


def _buffett_section(positions: dict) -> str:
    if not positions:
        return ""
    try:
        from value_filter import evaluate as _value_eval
    except Exception as e:
        return f"<h3>¿Qué diría Buffett?</h3><p><em>No se pudo cargar el filtro: {e}</em></p>"

    bucket_emoji = {"resistente": "🛡️", "neutro": "⚪", "vulnerable": "⚠️"}
    rows = [_row(["Símbolo", "Pasa", "Score", "Sector", "IA", "Razones"], header=True)]
    for sym in positions.keys():
        try:
            v = _value_eval(sym)
            flag = "✅" if v.passes else "❌"
            ia = f"{bucket_emoji.get(v.ai_bucket, '⚪')} {v.ai_bucket}"
            rows.append(_row([sym, flag, v.score, v.sector or "-", ia, ", ".join(v.reasons[:6])]))
        except Exception as e:
            rows.append(_row([sym, "?", "-", "-", "-", f"error: {e}"]))
    table = "<table>" + "\n".join(rows) + "</table>"
    return f"<h3>¿Qué diría Buffett? <small>(análisis actual de posiciones abiertas)</small></h3>{table}"


def _cape_banner():
    try:
        from shiller_filter import get_current_cape, _zone
        cape, src, ts = get_current_cape()
    except Exception as e:
        return f'<section style="background:#3a2a0e;border-color:#8b6914;"><strong>⚠️ CAPE no disponible:</strong> {e}</section>'
    zone = _zone(cape)
    zone_labels = {"barato": ("🟢", "#0f2a16", "#238636"),
                   "normal": ("🟢", "#0f2a16", "#238636"),
                   "caro": ("🟡", "#3a2a0e", "#8b6914"),
                   "muy_caro": ("🟠", "#3a1e0e", "#8b4d1f"),
                   "burbuja": ("🔴", "#3a0e0e", "#8b1f1f")}
    emoji, bg, border = zone_labels.get(zone, ("⚪", "#161b22", "#30363d"))
    threshold = os.getenv("SHILLER_CAPE_MAX", "40")
    status = "bloquea entradas nuevas" if cape > float(threshold) else "permite entradas"
    return (f'<section style="background:{bg};border-color:{border};">'
            f'<strong>{emoji} CAPE Shiller S&P 500: {cape:.2f}</strong> '
            f'— zona {zone.replace("_"," ")}. Umbral {threshold} → {status}. '
            f'<small>fuente: {src}, {ts[:10]}</small>'
            f'</section>')


def _last_run_banner():
    status = _load("logs/last_run.json")
    if not status:
        return '<section style="background:#3a2a0e;border-color:#8b6914;"><strong>⚠️ Última corrida:</strong> sin registro (el bot todavía no ha corrido con el sistema de monitoreo nuevo).</section>'
    ran_at = status.get("ran_at", "?")
    swing_ok = status.get("swing_ok", False)
    value_ok = status.get("value_ok", False)
    all_ok = swing_ok and value_ok
    try:
        ran_dt = datetime.fromisoformat(ran_at.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - ran_dt).total_seconds() / 3600
        age_str = f"hace {age_h:.0f}h" if age_h >= 1 else f"hace {age_h*60:.0f} min"
    except Exception:
        age_str = ran_at
    if all_ok:
        return f'<section style="background:#0f2a16;border-color:#238636;"><strong>✅ Última corrida OK</strong> — {age_str} ({ran_at[:19]} UTC). Ambos bots conectaron al Gateway.</section>'
    parts = []
    if not swing_ok:
        parts.append(f"Swing-US (exit={status.get('swing_exit')})")
    if not value_ok:
        parts.append(f"Value-LATAM (exit={status.get('value_exit')})")
    return f'<section style="background:#3a0e0e;border-color:#8b1f1f;"><strong>❌ Última corrida FALLÓ</strong> — {age_str} ({ran_at[:19]} UTC). Falló: {", ".join(parts)}. Revisa si el IB Gateway está abierto.</section>'


def render():
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    banner = _last_run_banner()
    cape_banner = _cape_banner()
    swing = render_sleeve("🇺🇸 Swing-US", "state.json", "logs/run-*.jsonl")
    value = render_sleeve("🌎 Value-LATAM", "state_value.json", "logs/value-*.jsonl")

    html = f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8"><title>Bolsa Bot Dashboard</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #0b1020; color: #e6edf3; margin: 0; padding: 20px; }}
  h1 {{ color: #58a6ff; border-bottom: 2px solid #30363d; padding-bottom: 8px; }}
  h2 {{ color: #f0f6fc; margin-top: 32px; }}
  h3 {{ color: #79c0ff; margin-top: 24px; font-size: 15px; }}
  section {{ background: #161b22; padding: 20px 24px; margin: 16px 0; border-radius: 8px; border: 1px solid #30363d; }}
  table {{ width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 13px; }}
  th, td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #30363d; }}
  th {{ background: #21262d; color: #8b949e; font-weight: 600; }}
  .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin: 12px 0; }}
  .metrics div {{ background: #0d1117; padding: 12px; border-radius: 6px; border: 1px solid #30363d; }}
  .metrics span {{ display: block; color: #8b949e; font-size: 11px; text-transform: uppercase; margin-bottom: 4px; }}
  .metrics strong {{ font-size: 20px; color: #e6edf3; }}
  .pos {{ color: #3fb950; }}
  .neg {{ color: #f85149; }}
  small {{ color: #8b949e; font-weight: normal; }}
  footer {{ margin-top: 32px; color: #8b949e; font-size: 11px; text-align: center; }}
</style></head><body>
<h1>📊 Bolsa Bot Dashboard</h1>
<p style="color:#8b949e">Generado: {ts}</p>
{banner}
{cape_banner}
{swing}
{value}
<footer>ib_swing_bot + value-LATAM · paper trading</footer>
</body></html>"""
    with open("dashboard.html", "w") as f:
        f.write(html)
    print("→ dashboard.html generado")


if __name__ == "__main__":
    render()
