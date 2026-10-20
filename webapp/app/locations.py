"""Job-search location preferences: country, regions, cities, work mode, relocation."""

import json

from . import db

# Regions offered as checkboxes. Countries without a list still work: the user types
# cities, or leaves both empty to mean "anywhere in the country".
REGIONS = {
    "Slovakia": ["Bratislavský kraj", "Trnavský kraj", "Trenčiansky kraj", "Nitriansky kraj",
                 "Žilinský kraj", "Banskobystrický kraj", "Prešovský kraj", "Košický kraj"],
    "Czechia": ["Hlavní město Praha", "Středočeský kraj", "Jihočeský kraj", "Plzeňský kraj",
                "Karlovarský kraj", "Ústecký kraj", "Liberecký kraj", "Královéhradecký kraj",
                "Pardubický kraj", "Kraj Vysočina", "Jihomoravský kraj", "Olomoucký kraj",
                "Zlínský kraj", "Moravskoslezský kraj"],
    "Austria": ["Wien", "Niederösterreich", "Oberösterreich", "Burgenland", "Steiermark",
                "Kärnten", "Salzburg", "Tirol", "Vorarlberg"],
    "Hungary": ["Budapest", "Pest", "Győr-Moson-Sopron", "Komárom-Esztergom", "Fejér",
                "Borsod-Abaúj-Zemplén", "Hajdú-Bihar", "Csongrád-Csanád", "Baranya"],
    "Poland": ["Mazowieckie", "Małopolskie", "Śląskie", "Dolnośląskie", "Wielkopolskie",
               "Łódzkie", "Pomorskie", "Podkarpackie"],
    "Germany": ["Bayern", "Baden-Württemberg", "Berlin", "Hamburg", "Hessen",
                "Nordrhein-Westfalen", "Niedersachsen", "Sachsen"],
}
COUNTRIES = list(REGIONS) + ["Other"]
MODES = {"onsite": "On-site", "hybrid": "Hybrid", "remote": "Fully remote"}

DEFAULT = {"country": "Slovakia", "other_country": "", "regions": [], "cities": "",
           "modes": ["onsite", "hybrid", "remote"], "remote_abroad": False, "relocate": False}


def get() -> dict:
    try:
        stored = json.loads(db.get_setting("location_prefs") or "{}")
    except json.JSONDecodeError:
        stored = {}
    return {**DEFAULT, **stored}


def save(prefs: dict) -> None:
    db.set_setting("location_prefs", json.dumps(prefs, ensure_ascii=False))


def from_form(form) -> dict:
    country = form.get("country", "Slovakia")
    if country not in COUNTRIES:
        country = "Slovakia"
    allowed = set(REGIONS.get(country, []))
    return {
        "country": country,
        "other_country": str(form.get("other_country", "")).strip()[:80] if country == "Other" else "",
        "regions": [r for r in form.getlist("regions") if r in allowed],
        "cities": str(form.get("cities", "")).strip()[:500],
        "modes": [m for m in form.getlist("modes") if m in MODES] or list(MODES),
        "remote_abroad": form.get("remote_abroad") == "on",
        "relocate": form.get("relocate") == "on",
    }


def describe(prefs: dict | None = None) -> str:
    """Plain-language summary for the prompts (location check, cover letter logistics)."""
    p = prefs or get()
    if p["country"] == "Other":
        country = p["other_country"] or "an unspecified country"
    else:
        country = p["country"]
    places = p["regions"] + [c.strip() for c in p["cities"].split(",") if c.strip()]
    where = f"{', '.join(places)} ({country})" if places else f"anywhere in {country}"
    modes = ", ".join(MODES[m].lower() for m in p["modes"])
    lines = [
        f"- Looking for work in: {where}",
        f"- Accepted work modes: {modes}",
        f"- Fully remote roles for employers outside {country}: "
        f"{'acceptable' if p['remote_abroad'] and 'remote' in p['modes'] else 'not wanted'}",
        f"- Willing to relocate: {'yes' if p['relocate'] else 'no - a role requiring relocation is a deal-breaker'}",
    ]
    return "\n".join(lines)
