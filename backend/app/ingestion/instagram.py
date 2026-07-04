"""Source Instagram-first via Apify (hashtag scraper) — [PHASE 2].

Apify renvoie des posts BRUTS (tous secteurs, toutes régions). On FILTRE pour ne
garder que le CHR en (pré-)ouverture en Île-de-France, on en tire
`{handle, nom, ville}`, puis (dans le pipeline) on backfill le SIREN et on
réutilise tout l'enrichissement existant.

Nécessite `APIFY_TOKEN` dans l'environnement (sinon no-op, fail-soft).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests

APIFY_ACTOR = "apify~instagram-hashtag-scraper"
PROFILE_ACTOR = "apify~instagram-profile-scraper"
# Garde-fou déterministe : au-delà, un compte est clairement établi (Le Palais
# = 200 posts). Volontairement haut : le LLM (qui lit les derniers posts) est le
# vrai discriminateur ; ceci n'attrape que l'évident + sert de plancher si le LLM
# est indisponible. NB : l'ÂGE des posts n'est PAS un critère (une pré-ouverture
# peut teaser pendant des mois) — seuls le volume énorme et le CONTENU tranchent.
POSTS_ESTABLISHED_HARD = 150
# Hashtags CHR-orientés. Mesuré : les tags CHR (restaurantparis 73 %,
# ouverturerestaurant 33 % de comptes CHR+IdF) sont 3-7x plus propres que les
# génériques (ouvertureprochaine & co ~10 %) — on gaspille beaucoup moins de
# posts (= de crédits Apify) sur des comptes hors-cible.
#   - Famille "CHR + ouverture" : double signal, meilleur rendement final.
#   - Famille "CHR + lieu" : gros volume, majorité d'établis -> le juge LLM
#     filtre la fraîcheur (garde seulement ce qui ouvre/vient d'ouvrir).
#   - 1 générique conservé pour la pré-ouverture pure (local encore sans nom CHR).
DEFAULT_HASHTAGS = [
    # CHR + ouverture (précision)
    "ouverturerestaurant", "nouveaurestaurantparis", "ouverturerestaurantparis",
    "nouveaucafeparis", "nouvellebrasserie",
    # CHR + lieu (volume, le juge filtre la fraîcheur)
    "restaurantparis", "cafeparis", "coffeeshopparis", "barparis",
    # pré-ouverture pure
    "ouvertureprochaine",
]

# Mots-clés CHR (dans nom/caption/hashtags).
CHR_KEYWORDS = (
    "restaurant", "resto", "cafe", "coffee", "coffeeshop", "bar", "brasserie",
    "boulangerie", "patisserie", "traiteur", "bistrot", "bistro", "pizzeria",
    "cuisine", "salon de the", "glacier", "creperie", "cave a vin", "bar a vin",
    "gastronomie", "food", "snack", "burger", "sushi", "ramen", "tacos",
)
# Indices Île-de-France (villes fréquentes + Paris).
IDF_HINTS = (
    "paris", "nanterre", "boulogne", "saint-denis", "st-denis", "montreuil",
    "creteil", "versailles", "issy", "levallois", "neuilly", "vincennes",
    "montrouge", "clichy", "asnieres", "courbevoie", "puteaux", "ivry", "vitry",
    "aubervilliers", "pantin", "bagnolet", "malakoff", "vanves", "charenton",
    "colombes", "rueil", "suresnes", "meudon", "sceaux", "antony",
)
IDF_DEPTS = ("75", "77", "78", "91", "92", "93", "94", "95")


def has_token() -> bool:
    return bool(os.getenv("APIFY_TOKEN"))


def _norm(text: Optional[str]) -> str:
    text = (text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _is_chr(text: str) -> bool:
    t = _norm(text)
    return any(kw in t for kw in CHR_KEYWORDS)


def _is_idf(text: str) -> bool:
    t = _norm(text)
    if any(h in t for h in IDF_HINTS):
        return True
    for m in re.findall(r"\b(\d{5})\b", t):
        if m[:2] in IDF_DEPTS:
            return True
    return False


def _post_text(post: Dict[str, Any]) -> str:
    return " ".join(filter(None, [
        post.get("ownerFullName"),
        post.get("caption"),
        " ".join(post.get("hashtags") or []),
        post.get("locationName"),
    ]))


def scrape_hashtags(
    hashtags: Optional[List[str]] = None, limit: int = 40, timeout: int = 300
) -> List[Dict[str, Any]]:
    """Appelle l'actor Apify. Renvoie les posts bruts (ou [] si pas de token/erreur)."""
    token = os.getenv("APIFY_TOKEN")
    if not token:
        return []
    url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items?token={token}"
    body = {"hashtags": hashtags or DEFAULT_HASHTAGS, "resultsLimit": limit}
    try:
        resp = requests.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def discover(posts: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Posts bruts -> [{handle, name, city}] : CHR + IdF, dédupliqués par handle.
    Fonction PURE (testable)."""
    seen: set = set()
    out: List[Dict[str, str]] = []
    for post in posts:
        handle = (post.get("ownerUsername") or "").strip()
        if not handle or handle in seen:
            continue
        text = _post_text(post)
        location = post.get("locationName") or ""
        if not _is_chr(text):
            continue
        if not _is_idf(f"{location} {post.get('caption', '')} {' '.join(post.get('hashtags') or [])}"):
            continue
        seen.add(handle)
        out.append({
            "handle": handle,
            "name": (post.get("ownerFullName") or handle).strip(),
            "city": _city_from_location(location),
            "type": _chr_type(text),  # pré-classé (validé CHR à la découverte)
            "caption": (post.get("caption") or "")[:300],  # pour le juge LLM
        })
    return out


# Fraîcheurs qui constituent une opportunité (le reste est rejeté par le juge).
FRESH_KEEP = ("opening", "just_opened")


def judge(candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Juge LLM — deux verdicts par compte, tous deux requis pour garder :

    1) FRAÎCHEUR : l'heuristique dit "CHR + IdF" mais pas si le lieu OUVRE ou est
       établi depuis 20 ans. Mesuré : ~30 % seulement des candidats sont de
       vraies ouvertures. Valeurs opening / just_opened / established / unknown.
    2) IDENTITÉ (`is_venue_owner`) : sous ces hashtags, ~1/3 des posts viennent
       de comptes MÉDIA/influenceurs qui PARLENT d'un lieu (3e personne : "@x
       s'installe") — le `ownerUsername` est alors le messager, pas le lieu.
       Récupérer le vrai handle depuis la légende est non fiable (mesuré : 1 fois
       sur 2 aucun mention propre, et parfois un faux — bout d'email…). Décision
       produit : on ne garde QUE les auto-annonces (le posteur EST le lieu), où
       le handle est fiable par construction. Les posts média sont rejetés.

    Garde uniquement `freshness ∈ {opening, just_opened}` ET `is_venue_owner`.
    Nettoie le nom et attache `freshness` (le pipeline en déduit le signal).

    Fail-soft : sans OPENAI_API_KEY (ou erreur) -> renvoie l'entrée inchangée
    (on retombe sur le seul filtre heuristique, sans garantie)."""
    key = os.getenv("OPENAI_API_KEY")
    if not key or not candidates:
        return candidates
    try:
        from openai import OpenAI
    except ImportError:
        return candidates

    listing = "\n".join(
        f'{i}. @{c["handle"]} | nom: {c["name"]} | lieu: {c.get("city")} '
        f'| légende: {c.get("caption", "")}'
        for i, c in enumerate(candidates)
    )
    system = (
        "Tu évalues des comptes Instagram sous des hashtags d'ouverture CHR (café, "
        "restaurant, bar, hôtel, brasserie, boulangerie, traiteur, salon de thé) en "
        "Île-de-France, pour un fournisseur B2B de luminaires/mobilier. Pour CHAQUE "
        "compte, donne DEUX verdicts :\n"
        "A) is_venue_owner (bool) : le compte qui poste EST-il l'établissement "
        "lui-même ? true si auto-annonce à la 1re personne ('on ouvre', 'notre "
        "nouvelle adresse', 'bientôt chez nous'). false si c'est un tiers "
        "(média/guide/influenceur/agrégateur/compte perso) qui parle d'un lieu à la "
        "3e personne ('@x s'installe', 'un nouveau resto ouvre').\n"
        "B) freshness d'après des indices EXPLICITES dans la légende :\n"
        "   - 'opening' : ouvre bientôt / pré-ouverture\n"
        "   - 'just_opened' : a ouvert il y a peu (< ~3 mois)\n"
        "   - 'established' : établi, AUCUN signal d'ouverture ; OU pas un vrai lieu "
        "CHR (marque, produit, autre secteur)\n"
        "   - 'unknown' : impossible à trancher\n"
        "En cas de doute sur la fraîcheur -> 'unknown'/'established', JAMAIS "
        "'opening'. En cas de doute sur l'identité -> is_venue_owner=false. "
        "Donne aussi un nom d'enseigne propre (sans emojis ni slogan). "
        "Réponds STRICTEMENT en JSON."
    )
    user = (
        f"Voici {len(candidates)} comptes.\n"
        'Format EXACT : {"results":[{"index":0,"is_venue_owner":true,'
        '"freshness":"opening|just_opened|established|unknown","name":"Enseigne"}]}\n\n'
        f"{listing}"
    )
    try:
        client = OpenAI(api_key=key)
        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        data = json.loads(completion.choices[0].message.content)
        by_index = {int(r["index"]): r for r in data.get("results", []) if "index" in r}
    except Exception:
        return candidates

    kept: List[Dict[str, str]] = []
    for i, c in enumerate(candidates):
        r = by_index.get(i)
        # Requiert les DEUX : vraie ouverture ET compte = le lieu (handle fiable).
        if r and r.get("freshness") in FRESH_KEEP and r.get("is_venue_owner") is True:
            c2 = dict(c)
            if r.get("name"):
                c2["name"] = str(r["name"]).strip()
            c2["freshness"] = r["freshness"]
            kept.append(c2)
    return kept


def scrape_profiles(handles: List[str], timeout: int = 180) -> Dict[str, Dict[str, Any]]:
    """Scrape les profils Instagram (actor profil) -> {username: profil}.
    2e passe, sur les seuls survivants (donc peu coûteuse). Fail-soft {} si pas de
    token/erreur. Chaque profil porte postsCount, biography, businessAddress
    (structuré), externalUrl(s), et latestPosts (légendes + timestamps)."""
    token = os.getenv("APIFY_TOKEN")
    if not token or not handles:
        return {}
    url = f"https://api.apify.com/v2/acts/{PROFILE_ACTOR}/run-sync-get-dataset-items?token={token}"
    try:
        resp = requests.post(url, json={"usernames": handles}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for p in data:
        u = (p.get("username") or "").strip().lower()
        if u:
            out[u] = p
    return out


def _clean_city(name: Optional[str]) -> str:
    """'Paris, France' -> 'Paris'. Défaut ''."""
    return (name or "").split(",")[0].strip()


def _struct_address(profile: Dict[str, Any]) -> Optional[str]:
    """Adresse structurée d'un compte business -> chaîne, ou None."""
    ba = profile.get("businessAddress") or {}
    street = (ba.get("street_address") or "").strip()
    zc = (ba.get("zip_code") or "").strip()
    city = _clean_city(ba.get("city_name"))
    parts = [p for p in (street, " ".join(x for x in (zc, city) if x)) if p]
    return ", ".join(parts) or None


def _profile_long_history(profile: Dict[str, Any], today: date, threshold_days: int = 150) -> bool:
    """True si l'exploitation dure depuis des mois => établi. On regarde le VIEUX
    (historique long), pas le récent (=inactivité, autre signal). Robuste : on
    exige PLUSIEURS posts anciens (historique soutenu), pas un seul — sinon un
    throwback / post épinglé flaguerait à tort une vraie nouvelle adresse.
    Déterministe, complète postsCount pour les comptes peu actifs mais anciens."""
    dates: List[date] = []
    for x in profile.get("latestPosts") or []:
        ts = (x.get("timestamp") or "")[:10]
        try:
            dates.append(datetime.strptime(ts, "%Y-%m-%d").date())
        except ValueError:
            continue
    if not dates:
        return False
    old = [d for d in dates if (today - d).days > threshold_days]
    return len(old) >= min(3, len(dates))


def _external_url(profile: Dict[str, Any]) -> Optional[str]:
    """URL de site (hors linktr.ee/agrégateurs) si disponible."""
    url = (profile.get("externalUrl") or "").strip()
    if url and "linktr.ee" not in url and "linktree" not in url:
        return url
    for e in profile.get("externalUrls") or []:
        u = (e.get("url") or "").strip()
        if u and "linktr.ee" not in u and "linktree" not in u:
            return u
    return url or None


def profile_enrich(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """3e étage : sur les survivants (auto-annonces fraîches), scrape le profil et
    écarte les lieux DÉJÀ ÉTABLIS (des mois d'exploitation -> DROP, cas Le Palais),
    en gardant pré-ouverture ET vient d'ouvrir, et en enrichissant au passage
    adresse(s)/email(s)/site/ville.

    - Règle : postsCount > POSTS_ESTABLISHED_HARD -> DROP (garde-fou + plancher).
    - Juge LLM : lit les ~6 derniers posts (légendes + dates). Discrimine sur le
      LONG historique d'exploitation, pas l'âge. 'established' -> drop ; 'recent'
      (ouvre / vient d'ouvrir) -> garde ; doute -> garde.
      Extrait aussi addresses[] et emails[] (bio + posts).

    Fail-soft : pas de profil (token/erreur) -> candidats inchangés (ni drop ni
    enrichissement). Pas de clé OpenAI -> seule la règle postsCount s'applique."""
    if not candidates:
        return candidates
    profiles = scrape_profiles([c["handle"] for c in candidates])
    if not profiles:
        return candidates  # scrape indispo : on ne casse rien

    # Contexte profil + garde-fous DÉTERMINISTES (fiables, reproductibles) :
    #  - postsCount énorme -> établi (Le Palais 200) ;
    #  - plus vieux post récent daté de +5 mois -> exploitation longue (attrape
    #    les comptes peu actifs mais anciens : 11 posts étalés sur 2 ans).
    today = date.today()
    survivors: List[Dict[str, Any]] = []
    for c in candidates:
        prof = profiles.get(c["handle"].lower()) or {}
        c["_profile"] = prof
        posts_count = prof.get("postsCount")
        if isinstance(posts_count, int) and posts_count > POSTS_ESTABLISHED_HARD:
            continue
        if _profile_long_history(prof, today):
            continue
        survivors.append(c)

    # Client LLM créé une fois, appelé UNITAIREMENT (1 profil / appel). Le batch
    # faisait fuiter adresses ET verdicts d'un compte à l'autre — en isolé, plus
    # de contamination et verdict reproductible.
    client = _openai_client()

    kept: List[Dict[str, Any]] = []
    for c in survivors:
        prof = c.pop("_profile", {}) or {}
        # Profil vide (scrape raté/privé) -> gardé sans juger (aucune preuve).
        has_data = bool(prof.get("latestPosts") or prof.get("postsCount") is not None)
        v = _judge_profile(client, c["handle"], c.get("name"), prof) if (client and has_data) else {}
        # Drop uniquement le vraiment ÉTABLI (des mois d'exploitation). On garde
        # pré-ouverture ET vient d'ouvrir. Sinon (pas de clé/erreur) on garde
        # (la règle postsCount a déjà filtré l'évident).
        if v.get("status") == "established":
            continue
        # --- enrichissement ---
        struct_addr = _struct_address(prof)
        struct_city = _clean_city((prof.get("businessAddress") or {}).get("city_name"))
        llm_addrs = [a for a in (v.get("addresses") or []) if a]
        llm_emails = [e for e in (v.get("emails") or []) if e]
        biz_email = (prof.get("businessEmail") or prof.get("public_email") or "").strip()

        addresses = ([struct_addr] if struct_addr else []) + [a for a in llm_addrs if a != struct_addr]
        emails = ([biz_email] if biz_email else []) + [e for e in llm_emails if e != biz_email]

        if addresses:
            c["address"] = addresses[0]
            c["extra_addresses"] = addresses[1:]
        if struct_city:
            c["city"] = struct_city  # vraie ville -> corrige les 'villes bancales'
        if emails:
            c["email"] = emails[0]
            c["extra_emails"] = emails[1:]
        website = _external_url(prof)
        if website:
            c["website"] = website
        kept.append(c)
    return kept


_PROFILE_SYSTEM = (
    "Tu analyses UN profil Instagram d'établissement CHR pour un fournisseur B2B. "
    "But : écarter les lieux DÉJÀ ÉTABLIS et garder ceux qui OUVRENT ou VIENNENT "
    "D'OUVRIR (encore en phase d'aménagement = bon prospect). status :\n"
    "- 'established' : opère depuis PLUSIEURS MOIS — historique de service récurrent "
    "(programme du mois, réservations, événements réguliers, nombreux posts "
    "d'exploitation sur une longue période). À ÉCARTER.\n"
    "- 'recent' : ouvre bientôt (travaux, 'bientôt', compte à rebours) OU vient "
    "d'ouvrir récemment (lancement, premières semaines, peu d'historique). À GARDER.\n"
    "Indices d'ÉTABLI dans la BIO ou les posts : 'depuis <année>', 'ouvert depuis', "
    "'X ans', 'anniversaire' / 'X an(s)' fêté, posts de service de plusieurs mois/"
    "années. Un COMPTE neuf ne veut pas dire établissement neuf (ex. bio 'nouveau "
    "compte, ouverts depuis 1995' = établi).\n"
    "RÈGLE : si une DATE d'ouverture récente/à venir est mentionnée, c'est 'recent'. "
    "L'ÂGE des posts seul ne compte pas (une pré-ouverture peut teaser des mois). Ce "
    "qui compte : y a-t-il un LONG historique d'EXPLOITATION ? DOUTE -> 'recent'. "
    "Extrait aussi, UNIQUEMENT depuis la bio/les posts de CE compte : addresses "
    "(adresses postales complètes) et emails. Réponds STRICTEMENT en JSON."
)


def _openai_client():
    """Client OpenAI, ou None (fail-soft : pas de clé / SDK absent / erreur)."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=key)
    except Exception:
        return None


def _judge_profile(client, handle: str, name: Optional[str], profile: Dict[str, Any]) -> Dict[str, Any]:
    """Juge UN profil (établi/récent + extraction adresses/emails). Appel isolé :
    aucune contamination entre comptes. Fail-soft {} en cas d'erreur."""
    latest = profile.get("latestPosts") or []
    recents = "\n".join(
        f'  - {(x.get("timestamp") or "?")[:10]} : {(x.get("caption") or "")[:180]}'
        for x in latest[:6]
    )
    block = (
        f'@{handle} | {name} | posts={profile.get("postsCount")} '
        f'| abonnés={profile.get("followersCount")} | catégorie={profile.get("businessCategoryName")}\n'
        f'bio: {(profile.get("biography") or "")[:250]}\n'
        f'derniers posts:\n{recents or "  (aucun)"}'
    )
    user = (
        "Profil :\n" + block + "\n\n"
        'Format EXACT : {"status":"established|recent","opening_date":"YYYY-MM-DD|null",'
        '"addresses":[],"emails":[]}'
    )
    try:
        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": _PROFILE_SYSTEM}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return json.loads(completion.choices[0].message.content)
    except Exception:
        return {}


def _chr_type(text: str) -> str:
    """Sous-type CHR à partir des mots-clés (le lead est déjà validé CHR)."""
    t = _norm(text)
    if "hotel" in t:
        return "hôtel"
    if "coffeeshop" in t or "coffee shop" in t:
        return "coffee shop"
    if any(k in t for k in ("cafe", "coffee", "salon de the", "boulangerie", "patisserie", "glacier")):
        return "café"
    if any(k in t for k in ("bar", "brasserie", "cave a vin", "bar a vin")):
        return "bar"
    if "traiteur" in t:
        return "traiteur"
    return "restaurant"


def _city_from_location(location: str) -> str:
    """Extrait une ville exploitable de locationName (ex: 'Nanterre Prefecture'
    -> 'Nanterre'). Défaut : 'Paris'."""
    loc = (location or "").strip()
    if not loc:
        return "Paris"
    # Premier segment avant une virgule / mot parasite.
    first = re.split(r"[,\-]", loc)[0].strip()
    return first or "Paris"
