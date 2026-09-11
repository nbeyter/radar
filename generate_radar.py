#!/usr/bin/env python3
"""
Perakende Elektrik Piyasası Radarı — haftalık otomatik üretim script'i.

Bu script GitHub Actions içinde her Pazartesi çalışır:
  1. Mevcut index.html'i okur, edition-meta ve arşiv listesini çıkarır.
  2. Mevcut sayıyı archive/edition-N.html olarak arşivler (repoya commit edilir,
     GitHub Pages üzerinden kalıcı bir URL alır).
  3. Anthropic API'yi web_search tool'uyla çağırıp bu haftanın Türkiye ve
     global perakende elektrik piyasası gelişmelerini araştırır.
  4. Yeni index.html'i aynı tasarım şablonuyla (scripts/template.html) üretir.

Ortam değişkenleri:
  ANTHROPIC_API_KEY  (zorunlu, repo secret olarak eklenir)
  MODEL_ID           (opsiyonel, varsayılan aşağıda tanımlı)

Hata durumunda script exit(1) ile çıkar ki GitHub Actions run'u FAILED
olarak işaretlensin ve repo sahibine bildirim gitsin — sessiz/bozuk bir
sayfa yayınlamaktansa hiç güncellememe tercih edilir.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(REPO_ROOT, "index.html")
TEMPLATE_PATH = os.path.join(REPO_ROOT, "template.html")
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")

# Docs'a göre güncel bir sürüm; bozulursa docs.claude.com/en/docs/about-claude/models
# adresinden güncel bir model id ile değiştirin.
MODEL_ID = os.environ.get("MODEL_ID", "claude-sonnet-4-5")
WEB_SEARCH_TOOL_VERSION = "web_search_20250305"

TR_MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
TR_MONTHS_FULL = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]


def tr_date_short(d: date) -> str:
    return f"{d.day} {TR_MONTHS[d.month - 1]} {d.year}"


def tr_period_label(start: date, end: date) -> str:
    if start.month == end.month and start.year == end.year:
        return f"{start.day}–{end.day} {TR_MONTHS_FULL[end.month - 1]} {end.year}"
    if start.year == end.year:
        return (
            f"{start.day} {TR_MONTHS_FULL[start.month - 1]} – "
            f"{end.day} {TR_MONTHS_FULL[end.month - 1]} {end.year}"
        )
    return (
        f"{start.day} {TR_MONTHS_FULL[start.month - 1]} {start.year} – "
        f"{end.day} {TR_MONTHS_FULL[end.month - 1]} {end.year}"
    )


def fail(msg: str):
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def parse_current_index(html: str) -> dict:
    meta_match = re.search(
        r'<div id="edition-meta" data-edition="(\d+)" data-period-start="([\d-]+)" '
        r'data-period-end="([\d-]+)" data-published="([\d-]+)" hidden></div>',
        html,
    )
    if not meta_match:
        fail("index.html içinde edition-meta bulunamadı — format değişmiş olabilir.")

    edition = int(meta_match.group(1))
    period_end = datetime.strptime(meta_match.group(3), "%Y-%m-%d").date()

    archive_ul_match = re.search(
        r'<ul id="archive-list" class="archive-list">(.*?)</ul>', html, re.S
    )
    archive_inner = archive_ul_match.group(1).strip() if archive_ul_match else ""

    # Mevcut trend panelini fallback olarak çıkar (bu hafta yeni rapor bulunamazsa kullanılır)
    trend = {}
    big = re.search(
        r'<span class="num">([^<]+)</span>\s*<span class="arrow">→</span>\s*'
        r'<span class="num">([^<]+)</span>\s*<span class="cagr">YBBO ([^<]+)</span>',
        html,
    )
    if big:
        trend["market_from"], trend["market_to"], trend["cagr"] = (
            big.group(1),
            big.group(2),
            big.group(3),
        )
    lead = re.search(r'<p class="lead">(.*?)</p>', html, re.S)
    trend["lead"] = lead.group(1).strip() if lead else ""
    players = re.findall(r'<span class="tag">([^<]+)</span>', html)
    trend["players"] = players
    bars = re.findall(
        r'<b>([^<]+)</b><span>([^<]+)</span></div>\s*'
        r'<div class="bar-track"><div class="bar-fill( alt)?" style="width:([\d.]+)%"></div>',
        html,
    )
    trend["bars"] = [
        {
            "label": b[0],
            "value_label": b[1],
            "style": "alt" if b[2].strip() else "main",
            "width_percent": float(b[3]),
        }
        for b in bars
    ]

    return {
        "edition": edition,
        "period_end": period_end,
        "archive_inner": archive_inner,
        "trend_fallback": trend,
        "raw_html": html,
    }


def archive_current_edition(current: dict, period_start_prev: date) -> str:
    """Mevcut sayıyı archive/edition-N.html olarak kaydeder, repo-içi görece linki döner."""
    edition = current["edition"]
    period_end = current["period_end"]
    label = tr_period_label(period_start_prev, period_end)
    archived_html = current["raw_html"].replace(
        "<title>Perakende Elektrik Radarı</title>",
        f"<title>Perakende Elektrik Radarı — Sayı {edition} ({label})</title>",
        1,
    )
    filename = f"edition-{edition}.html"
    write_text(os.path.join(ARCHIVE_DIR, filename), archived_html)
    return f"archive/{filename}", label


RESEARCH_PROMPT_TEMPLATE = """\
Sen Yepas (Türkiye'de perakende elektrik şirketi) için haftalık bir "Perakende \
Elektrik Piyasası Radarı" hazırlayan bir araştırmacısın. Görevin, {period_start} \
ile {period_end} arasındaki (yeterli haber yoksa en güncel doğrulanabilir \
gelişmeler) döneme ait GERÇEK ve DOĞRULANABİLİR haberleri web_search tool'unu \
kullanarak taraman ve sonucu KATI bir JSON şemasında döndürmen.

KAPSAM 1 — Türkiye perakende elektrik piyasası: EPDK düzenlemeleri/lisans \
kararları, Enerjisa, Aydem Perakende/Aydem Enerji, Zorlu Enerji, Sepaş Enerji/ \
Sepaşcharge, AEDAŞ, CK Enerji/CK Boğaziçi, Bereket Enerji, Limak Enerji, \
Naturelgaz gibi şirketlerin yeni ürün/hizmet, tarife, kampanya veya düzenleme \
haberleri.

KAPSAM 2 — Global perakende elektrik piyasası: Octopus Energy, E.ON/E.ON Next, \
EDF Energy, Enel/Enel X, Iberdrola, ENGIE, Vistra/TXU Energy, National Grid, \
Shell Energy, Duke Energy, NextEra gibi öncü tedarikçilerin yeni ürün/hizmet \
duyuruları (ev bataryası, EV şarj, dinamik/saatlik tarife, sanal santral/VPP, \
yeşil enerji paketleri vb.) ve genel pazar trend raporları.

KURALLAR:
- Yalnızca kaynağı gerçek bir web araması sonucunda bulduğun, doğrulanabilir \
haberleri kullan. Uydurma şirket haberi EKLEME. Bir kategori için yeterli \
tazelikte haber yoksa o kategoriyi kısa tut / boş bırak.
- Her karta gerçek bir kaynak URL'si koy.
- Türkiye'den en az 3, global'den en az 3 kart bulmaya çalış; bulamazsan daha \
azını döndür, asla uydurma ile doldurma.
- Küresel pazar büyüklüğü/CAGR için son 30 gün içinde yeni bir analist raporu \
bulursan trend_update_available=true yap ve rakamları doldur; bulamazsan \
trend_update_available=false döndür (mevcut rakamlar korunacak).
- pill alanı şu değerlerden biri olmalı: "product" (yeni ürün/hizmet), \
"reg" (düzenleme), "campaign" (kampanya/sürdürülebilirlik), "analysis" \
(pazar analizi/finansman/rapor).
- date alanı kısa Türkçe formatta olmalı (ör. "6 Eyl 2026").
- insights alanında bu haftanın 4 ana çıkarımını (temayı) TR+global \
gelişmeleri sentezleyerek yaz.
- footer_note_extra alanına, bu hafta yeterli tazelikte haberi bulunmayan \
şirketleri kısaca listeleyen bir cümle yaz (varsa).

SADECE aşağıdaki şemaya uyan bir JSON döndür, başka hiçbir metin ekleme, \
```json ile başlayıp ``` ile bitir:

```json
{{
  "insights": [
    {{"heading": "01 — BAŞLIK", "body": "..."}},
    {{"heading": "02 — BAŞLIK", "body": "..."}},
    {{"heading": "03 — BAŞLIK", "body": "..."}},
    {{"heading": "04 — BAŞLIK", "body": "..."}}
  ],
  "tr_cards": [
    {{"org": "...", "date": "...", "pill": "reg", "title": "...", "body": "...", "url": "https://..."}}
  ],
  "global_cards": [
    {{"org": "...", "date": "...", "pill": "product", "title": "...", "body": "...", "url": "https://..."}}
  ],
  "trend_update_available": false,
  "trend": {{
    "market_from": "$3,19T",
    "market_to": "$4,04T",
    "cagr": "%4,86",
    "lead": "...",
    "players": ["...", "..."],
    "bars": [
      {{"label": "...", "value_label": "...", "style": "main", "width_percent": 50}}
    ]
  }},
  "footer_note_extra": "...",
  "sources_list": ["EPDK", "..."]
}}
```
"""


def call_anthropic(period_start: date, period_end: date) -> dict:
    try:
        import anthropic
    except ImportError:
        fail("anthropic paketi kurulu değil — requirements.txt kontrol edin.")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        fail("ANTHROPIC_API_KEY ortam değişkeni ayarlanmamış (repo secret ekleyin).")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = RESEARCH_PROMPT_TEMPLATE.format(
        period_start=period_start.isoformat(), period_end=period_end.isoformat()
    )

    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=8000,
        tools=[
            {
                "type": WEB_SEARCH_TOOL_VERSION,
                "name": "web_search",
                "max_uses": 20,
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    full_text = "\n".join(text_parts)

    json_match = re.search(r"```json\s*(.*?)\s*```", full_text, re.S)
    if not json_match:
        # ```json fence bulunamadıysa, ilk { ile son } arasını dene
        brace_match = re.search(r"\{.*\}", full_text, re.S)
        if not brace_match:
            fail("Model yanıtından JSON çıkarılamadı. Ham yanıt:\n" + full_text[:2000])
        json_str = brace_match.group(0)
    else:
        json_str = json_match.group(1)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        fail(f"JSON parse hatası: {e}\nHam JSON:\n{json_str[:2000]}")

    return data


PILL_LABELS = {
    "product": "Yeni Ürün/Hizmet",
    "reg": "Düzenleme",
    "campaign": "Kampanya",
    "analysis": "Pazar Analizi",
}
PILL_ACCENT = {
    "product": "var(--accent)",
    "reg": "var(--accent2)",
    "campaign": "var(--good)",
    "analysis": "var(--info)",
}


def render_card(c: dict) -> str:
    pill = c.get("pill", "analysis")
    if pill not in PILL_LABELS:
        pill = "analysis"
    return f"""          <div class="card" style="--card-accent:{PILL_ACCENT[pill]}">
            <div class="card-top"><span class="card-org">{c['org']}</span><span class="card-date mono">{c['date']}</span></div>
            <span class="pill {pill}">{PILL_LABELS[pill]}</span>
            <h4>{c['title']}</h4>
            <p>{c['body']}</p>
            <a class="src" href="{c['url']}" target="_blank" rel="noopener">Kaynak</a>
          </div>"""


def render_insight(i: int, ins: dict) -> str:
    return f"""      <div class="insight">
        <div class="n">{ins['heading']}</div>
        <p>{ins['body']}</p>
      </div>"""


def render_bar(b: dict) -> str:
    alt = " alt" if b.get("style") == "alt" else ""
    return f"""          <div class="bar-row">
            <div class="bar-label"><b>{b['label']}</b><span>{b['value_label']}</span></div>
            <div class="bar-track"><div class="bar-fill{alt}" style="width:{b['width_percent']}%"></div></div>
          </div>"""


def main():
    if not os.path.exists(INDEX_PATH):
        fail("index.html bulunamadı — repo kökünde olmalı.")

    current = parse_current_index(read_text(INDEX_PATH))
    old_edition = current["edition"]
    new_edition = old_edition + 1
    period_start = current["period_end"] + timedelta(days=1)
    period_end = date.today()
    if period_end <= period_start:
        period_end = period_start  # tek günlük dönem, en kötü ihtimalle

    # Önceki dönemin başlangıcı bilinmiyor (sadece bitişi meta'da var);
    # arşiv etiketinde period_end - 6 gün varsayımıyla makul bir aralık kullanılır.
    prev_period_start_guess = current["period_end"] - timedelta(days=6)
    archive_href, archived_label = archive_current_edition(current, prev_period_start_guess)

    ai = call_anthropic(period_start, period_end)

    tr_cards = ai.get("tr_cards", [])
    global_cards = ai.get("global_cards", [])
    insights = ai.get("insights", [])
    record_count = len(tr_cards) + len(global_cards)

    if ai.get("trend_update_available") and ai.get("trend"):
        trend = ai["trend"]
        trend_note_suffix = ""
    else:
        trend = current["trend_fallback"]
        trend_note_suffix = " <i>Bu hafta yeni bir küresel pazar raporu bulunamadığından rakamlar önceki sayıdan değişmeden korunmuştur.</i>"

    insights_html = "\n".join(render_insight(i, x) for i, x in enumerate(insights, 1))
    tr_cards_html = "\n\n".join(render_card(c) for c in tr_cards) if tr_cards else (
        '          <p style="color:var(--ink-soft);font-style:italic;font-size:13.5px;">'
        "Bu hafta Türkiye tarafında doğrulanabilir yeni gelişme bulunamadı.</p>"
    )
    global_cards_html = "\n\n".join(render_card(c) for c in global_cards) if global_cards else (
        '          <p style="color:var(--ink-soft);font-style:italic;font-size:13.5px;">'
        "Bu hafta global tarafta doğrulanabilir yeni gelişme bulunamadı.</p>"
    )
    players_html = "\n".join(f'            <span class="tag">{p}</span>' for p in trend.get("players", []))
    bars_html = "\n".join(render_bar(b) for b in trend.get("bars", []))

    new_archive_item = (
        f'      <li class="archive-item"><span class="ed">Sayı {old_edition}</span>'
        f'<span class="period">{archived_label}</span>'
        f'<a href="{archive_href}" target="_blank" rel="noopener">Görüntüle</a></li>'
    )
    prev_archive_inner = current["archive_inner"]
    if "archive-empty" in prev_archive_inner:
        archive_list_html = new_archive_item
    else:
        archive_list_html = new_archive_item + "\n" + prev_archive_inner

    footer_note = (
        "Bu sayı, EPDK duyuruları, sektör basını ve global tedarikçi/analist "
        "kaynaklarından derlenen <b style=\"color:var(--ink)\">doğrulanabilir</b> "
        "gelişmeleri yansıtır ve bir GitHub Actions workflow'u tarafından "
        "Anthropic API (web araması) kullanılarak otomatik üretilmiştir."
    )
    if ai.get("footer_note_extra"):
        footer_note += " " + ai["footer_note_extra"]

    sources_list = ai.get("sources_list") or []
    footer_sources = "Kaynaklar: " + ", ".join(sources_list) if sources_list else "Kaynaklar: kart bazında belirtilmiştir"

    template = read_text(TEMPLATE_PATH)
    replacements = {
        "%%EDITION%%": str(new_edition),
        "%%PERIOD_START%%": period_start.isoformat(),
        "%%PERIOD_END%%": period_end.isoformat(),
        "%%PUBLISHED%%": date.today().isoformat(),
        "%%PERIOD_LABEL%%": tr_period_label(period_start, period_end),
        "%%RECORD_COUNT%%": str(record_count),
        "%%TR_COUNT%%": str(len(tr_cards)),
        "%%GLOBAL_COUNT%%": str(len(global_cards)),
        "%%INSIGHTS_HTML%%": insights_html,
        "%%TR_CARDS_HTML%%": tr_cards_html,
        "%%GLOBAL_CARDS_HTML%%": global_cards_html,
        "%%TREND_YEAR_RANGE%%": f"{date.today().year}-{date.today().year + 5}",
        "%%TREND_FROM%%": trend.get("market_from", ""),
        "%%TREND_TO%%": trend.get("market_to", ""),
        "%%TREND_CAGR%%": trend.get("cagr", ""),
        "%%TREND_LEAD%%": trend.get("lead", "") + trend_note_suffix,
        "%%TREND_PLAYERS_HTML%%": players_html,
        "%%TREND_BARS_HTML%%": bars_html,
        "%%ARCHIVE_COUNT_LABEL%%": f"{new_edition - 1} sayı yayında",
        "%%ARCHIVE_LIST_HTML%%": archive_list_html,
        "%%FOOTER_NOTE%%": "      " + footer_note,
        "%%FOOTER_SOURCES%%": footer_sources,
    }
    new_html = template
    for token, value in replacements.items():
        new_html = new_html.replace(token, value)

    write_text(INDEX_PATH, new_html)

    print(f"Sayı {new_edition} üretildi. Dönem: {tr_period_label(period_start, period_end)}")
    print(f"Türkiye: {len(tr_cards)} kayıt, Global: {len(global_cards)} kayıt")
    print(f"Sayı {old_edition} arşivlendi -> {archive_href}")


if __name__ == "__main__":
    main()
