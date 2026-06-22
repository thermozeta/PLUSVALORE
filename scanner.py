import os
import json
import requests
import pytz
from datetime import datetime, timedelta

# ── Variabili d'ambiente (impostate come GitHub Secrets) ──────────────────────
TOTALCORNER_TOKEN  = os.environ["TOTALCORNER_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
ONESIGNAL_APP_ID   = os.environ.get("ONESIGNAL_APP_ID", "")
ONESIGNAL_API_KEY  = os.environ.get("ONESIGNAL_API_KEY", "")

TC_BASE = "https://api.totalcorner.com/v1"

# ── Carica configurazione filtri ──────────────────────────────────────────────
def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

# ── TotalCorner: partite di oggi ──────────────────────────────────────────────
def get_today_matches():
    r = requests.get(f"{TC_BASE}/today", params={"token": TOTALCORNER_TOKEN}, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])

# ── TotalCorner: statistiche angoli di una squadra ───────────────────────────
def get_team_corner_stats(team_id):
    r = requests.get(f"{TC_BASE}/team/stats/{team_id}", params={"token": TOTALCORNER_TOKEN}, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", {})
    return {
        "avg_get":   float(data.get("average_get",    0)),   # media angoli guadagnati
        "avg_lost":  float(data.get("average_lost",   0)),   # media angoli concessi
        "over95_pct": float(data.get("over95_percent", 0)),  # % Over 9.5 storico
    }

# ── Applica il filtro Plusvalore ──────────────────────────────────────────────
def apply_filter(home_stats, away_stats, cfg):
    combined = round(home_stats["avg_get"] + away_stats["avg_get"], 1)
    over_pct  = round((home_stats["over95_pct"] + away_stats["over95_pct"]) / 2, 1)
    passes = (
        combined >= cfg["min_combined_avg"] and
        over_pct  >= cfg["min_over_pct"]
    )
    return passes, combined, over_pct

# ── Genera analisi AI con Claude Haiku ───────────────────────────────────────
def generate_analysis(home, away, home_avg, away_avg, combined, over_pct, league):
    prompt = (
        f"Sei un analista di scommesse sugli angoli. Scrivi ESATTAMENTE 2 righe di analisi "
        f"in italiano per questa partita:\n"
        f"{home} vs {away} ({league})\n"
        f"Media angoli {home}: {home_avg} | Media angoli {away}: {away_avg}\n"
        f"Combinata: {combined} | Over 9.5 storico: {over_pct}%\n"
        f"Solo 2 righe, tono tecnico, nessun testo aggiuntivo."
    )
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 120,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()

# ── Invia messaggio Telegram ──────────────────────────────────────────────────
def send_telegram(pick):
    badge = "🔥 SEGNALE FORTE" if pick["signal"] == "strong" else "✅ SEGNALE"
    msg = (
        f"{badge} | PLUSVALORE\n\n"
        f"⚽ {pick['home']} vs {pick['away']}\n"
        f"🏆 {pick['league']}\n"
        f"⏰ Kick-off: {pick['time']}\n\n"
        f"📐 Analisi Angoli\n"
        f"• Media casa: {pick['home_avg']} ang/partita\n"
        f"• Media trasferta: {pick['away_avg']} ang/partita\n"
        f"• Combinata: {pick['combined']}\n"
        f"• Over 9.5 storico: {pick['over_pct']}%\n\n"
        f"💡 {pick['analysis']}\n\n"
        f"🎯 PICK: Over 9.5 Angoli\n"
        f"📊 Track record → livepick.com/plusvalore"
    )
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHANNEL_ID, "text": msg},
        timeout=30,
    )
    r.raise_for_status()
    print(f"  ✓ Telegram inviato: {pick['home']} vs {pick['away']}")

# ── Invia push notification OneSignal ─────────────────────────────────────────
def send_push(pick):
    if not ONESIGNAL_APP_ID or not ONESIGNAL_API_KEY:
        return
    requests.post(
        "https://onesignal.com/api/v1/notifications",
        headers={"Authorization": f"Basic {ONESIGNAL_API_KEY}", "Content-Type": "application/json"},
        json={
            "app_id": ONESIGNAL_APP_ID,
            "included_segments": ["All"],
            "headings": {"it": f"⚽ {pick['home']} vs {pick['away']}"},
            "contents": {"it": f"🎯 Over 9.5 Angoli · {pick['time']} · {pick['league']}"},
        },
        timeout=30,
    )
    print(f"  ✓ Push inviato: {pick['home']} vs {pick['away']}")

# ── Salva picks.json ──────────────────────────────────────────────────────────
def save_picks(data):
    with open("picks.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Aggiorna history.json con i pick del giorno ───────────────────────────────
def update_history(today_picks):
    try:
        with open("history.json", "r") as f:
            history = json.load(f)
    except Exception:
        history = {"picks": []}

    existing_ids = {p["id"] for p in history["picks"]}
    for p in today_picks:
        if p["id"] not in existing_ids:
            history["picks"].insert(0, p)

    with open("history.json", "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    cfg  = load_config()
    now  = datetime.now(pytz.UTC)
    today = now.strftime("%Y-%m-%d")

    alert_start = now + timedelta(hours=cfg["alert_hours_before"] - 0.25)
    alert_end   = now + timedelta(hours=cfg["alert_hours_before"] + 0.25)

    # Carica picks odierni esistenti
    try:
        with open("picks.json", "r") as f:
            picks_data = json.load(f)
        if picks_data.get("date") != today:
            picks_data = {"date": today, "last_scan": None, "total_scanned": 0, "picks": []}
    except Exception:
        picks_data = {"date": today, "last_scan": None, "total_scanned": 0, "picks": []}

    alerted_ids = {p["id"] for p in picks_data["picks"]}

    print(f"\n🔍 Scansione in corso — {now.strftime('%H:%M UTC')}")
    matches = get_today_matches()
    print(f"   Partite trovate oggi: {len(matches)}")

    for match in matches:
        match_id = str(match.get("id", ""))
        try:
            # Parsing orario (adatta al formato reale TotalCorner)
            match_time = datetime.fromisoformat(
                match.get("date") or match.get("time") or ""
            ).replace(tzinfo=pytz.UTC)
        except Exception:
            continue

        try:
            home_stats = get_team_corner_stats(match.get("home_id") or match.get("h_id"))
            away_stats = get_team_corner_stats(match.get("away_id") or match.get("a_id"))
        except Exception as e:
            print(f"   ✗ Stats non disponibili per {match_id}: {e}")
            continue

        passes, combined, over_pct = apply_filter(home_stats, away_stats, cfg)
        if not passes:
            continue

        home     = match.get("home") or match.get("h", "")
        away     = match.get("away") or match.get("a", "")
        league   = match.get("league") or match.get("l", "")
        home_avg = home_stats["avg_get"]
        away_avg = away_stats["avg_get"]
        signal   = "strong" if combined >= 11.0 and over_pct >= 70 else "medium"

        # Aggiungi ai pick se nuovo
        if match_id not in alerted_ids:
            try:
                analysis = generate_analysis(home, away, home_avg, away_avg, combined, over_pct, league)
            except Exception:
                analysis = f"{home} e {away} mostrano una media combinata di {combined} angoli per partita."

            pick = {
                "id":        match_id,
                "date":      today,
                "time":      match_time.strftime("%H:%M"),
                "timestamp": match_time.isoformat(),
                "league":    league,
                "home":      home,
                "away":      away,
                "home_avg":  home_avg,
                "away_avg":  away_avg,
                "combined":  combined,
                "over_pct":  over_pct,
                "signal":    signal,
                "analysis":  analysis,
                "alerted":   False,
                "result":    None,
            }
            picks_data["picks"].append(pick)
            alerted_ids.add(match_id)
            print(f"   ✓ Qualificante: {home} vs {away} (combinata {combined})")

        # Invia alert se siamo nella finestra temporale
        pick_obj = next((p for p in picks_data["picks"] if p["id"] == match_id), None)
        if pick_obj and not pick_obj["alerted"] and alert_start <= match_time <= alert_end:
            try:
                send_telegram(pick_obj)
                send_push(pick_obj)
                pick_obj["alerted"] = True
            except Exception as e:
                print(f"   ✗ Errore invio alert: {e}")

    picks_data["last_scan"]     = now.isoformat()
    picks_data["total_scanned"] = len(matches)
    save_picks(picks_data)
    update_history(picks_data["picks"])
    print(f"   Pick qualificanti oggi: {len(picks_data['picks'])}\n")

if __name__ == "__main__":
    main()
