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
from datetime import date
from typing import Any, Dict, List, Optional

import requests

from . import profile_guards
from .enrichment.siret_matcher import _age_label

# Sentinel : "résous le client OpenAI depuis l'env". Passer None = SANS juge
# (déterministe, aucun appel LLM — indispensable pour les tests / fail-soft).
_USE_ENV = object()

APIFY_ACTOR = "apify~instagram-hashtag-scraper"
PROFILE_ACTOR = "apify~instagram-profile-scraper"
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


def classify_profiles(
    candidates: List[Dict[str, Any]],
    profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    *, match_fn=None, client=_USE_ENV, today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Étiquette CHAQUE candidat (survivant de discover) d'un label de cycle de
    vie. Chaîne : garde-fous déterministes (profile_guards) -> sinon matcher SIRET
    (AVANT le juge, pour que le dossier inclue le registre) -> juge unitaire
    (judge_dossier). Enrichit au passage adresse/email/site/ville des comptes non
    écartés par les gardes. Fonction sans DB : `match_fn` et `client` injectables
    (tests/éval sans réseau). Renvoie TOUS les candidats annotés `label`,
    `confidence`, `_match` (+ enrichissement) — le FILTRAGE (quel label devient un
    lead) est la responsabilité de run_instagram."""
    if not candidates:
        return candidates
    today = today or date.today()
    profiles = profiles or {}
    resolved_client = _openai_client() if client is _USE_ENV else client

    out: List[Dict[str, Any]] = []
    for c in candidates:
        prof = profiles.get(c["handle"].lower()) or {}
        has_data = bool(prof.get("latestPosts") or prof.get("postsCount") is not None)

        # 1. Garde-fous déterministes (gratuits, avant tout LLM).
        guard = profile_guards.guard_verdict(prof, today) if has_data else None
        if guard:
            c["label"] = guard
            c["confidence"] = "haute"
            c["_match"] = None
            out.append(c)
            continue

        # 2. Pré-enrichissement dispo immédiatement (nourrit le matcher).
        struct_addr = _struct_address(prof)
        struct_city = _clean_city((prof.get("businessAddress") or {}).get("city_name"))
        if struct_addr:
            c["address"] = struct_addr
        c["bio_snippet"] = (prof.get("biography") or "")[:300]

        # 3. Matcher SIRET AVANT le juge (le dossier inclut le registre).
        # COÛT ASSUMÉ [revue finale] : le matcher (recherche + geocode Sirene, plus
        # arbitre LLM si pool ambigu) tourne pour CHAQUE candidate survivant aux
        # gardes, y compris celles que le juge écartera ensuite (established/
        # not_venue non pris par les gardes déterministes). Délibéré (le dossier du
        # juge doit contenir le registre) mais à surveiller côté quotas/rate-limit
        # Sirene et budget arbitre.
        match = match_fn(c) if match_fn else None
        c["_match"] = match

        # 4. Juge unitaire (fail-soft : doute -> unknown = gardé).
        verdict = (judge_dossier(resolved_client, c["handle"], c.get("name"), prof,
                                 caption=c.get("caption"), match_result=match, today=today)
                   if (resolved_client and has_data) else {})
        c["label"] = verdict.get("label") or "unknown"
        c["confidence"] = verdict.get("confidence") or ("basse" if not verdict else "moyenne")

        # 5. Post-enrichissement (utile aux leads gardés).
        llm_addrs = [a for a in (verdict.get("addresses") or []) if a]
        llm_emails = [e for e in (verdict.get("emails") or []) if e]
        biz_email = (prof.get("businessEmail") or prof.get("public_email") or "").strip()
        addresses = ([struct_addr] if struct_addr else []) + [a for a in llm_addrs if a != struct_addr]
        emails = ([biz_email] if biz_email else []) + [e for e in llm_emails if e != biz_email]
        if addresses:
            c["address"] = addresses[0]
            c["extra_addresses"] = addresses[1:]
        if struct_city:
            c["city"] = struct_city
        if emails:
            c["email"] = emails[0]
            c["extra_emails"] = emails[1:]
        website = _external_url(prof)
        if website:
            c["website"] = website
        out.append(c)
    return out


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


_DOSSIER_SYSTEM = (
    "Tu étiquettes le CYCLE DE VIE d'UN compte Instagram d'établissement CHR "
    "(café, restaurant, bar, hôtel, brasserie, boulangerie, traiteur, salon de "
    "thé) en Île-de-France, pour un fournisseur B2B de luminaires/mobilier. On te "
    "donne un dossier complet : bio, compteurs, catégorie, derniers posts DATÉS "
    "(âge déjà calculé), légende de découverte, et le résultat du registre Sirene "
    "(enseigne, NAF, âge de la société). Choisis UN label :\n"
    "- opening_soon : ouvre bientôt / pré-ouverture (travaux, 'bientôt', compte à "
    "rebours, aucune exploitation en cours).\n"
    "- just_opened : a ouvert il y a peu (premières semaines/mois, société créée "
    "récemment, peu d'historique d'exploitation).\n"
    "- established : opère depuis des mois/années (historique de service, "
    "'depuis <année>', anniversaire, société ancienne au registre).\n"
    "- renovation : établissement à l'apparence ÉTABLIE (ancienneté déclarée, "
    "long historique, horaires) actuellement EN TRAVAUX / rénovation, OU rouvert "
    "il y a quelques JOURS à peine après un chantier dont les posts montrent "
    "encore les finitions. Segment CHAUD (fenêtre d'aménagement ouverte "
    "MAINTENANT).\n"
    "- chain_multisite : marque à PLUSIEURS adresses OU en EXPANSION — plusieurs "
    "lieux listés, OU la bio/les posts annoncent une 2e adresse ou un 2e "
    "établissement de la MÊME enseigne (ex. '<enseigne> 2', 'nouvelle adresse', "
    "ouverture d'une succursale). Décor centralisé, non prioritaire (reste un "
    "lead 'en base').\n"
    "- not_venue : pas un établissement CHR (marque, produit, agence, média, hors "
    "France).\n"
    "- noise : compte quasi mort / sans valeur (aucun contenu exploitable).\n"
    "- unknown : impossible de trancher.\n"
    "RÈGLES : NOT_VENUE PRIORITAIRE — d'abord : ce compte est-il un établissement "
    "CHR physique en France ? Marque/produit/hors France/média -> not_venue, quel "
    "que soit le reste. LOCALISATION D'ABORD : si les posts ou la bio situent le "
    "lieu HORS DE FRANCE (ville étrangère — Namur, Bruxelles, Liège, Genève, "
    "Lausanne, Montréal… —, domaine .be/.ch/.ca), c'est not_venue MÊME s'il annonce "
    "une ouverture. De même, un COMMERCE DE DÉTAIL alimentaire sans salle "
    "(boucherie, charcuterie, fromagerie, épicerie, supérette) n'est pas un lieu "
    "CHR -> not_venue. MAIS un établissement CHR physique en France qui prépare "
    "son ouverture (date d'ouverture, travaux, 'coming soon') reste opening_soon "
    "ou just_opened, JAMAIS not_venue — un nom qui sonne comme une marque "
    "(Villa, Maison, un prénom) n'en fait pas une marque. COMPTE ÉPARS : un "
    "compte à peu de posts/abonnés et bio quasi vide n'est PAS not_venue ni "
    "noise si ses posts/légendes portent des indices d'ouverture (« l'équipe "
    "est prête », « bientôt », « coming soon », compte à rebours, « nouvelle "
    "adresse ») ET un contexte CHR français (plat/cuisine, nom de nourriture, "
    "ville française) — c'est alors opening_soon ou just_opened. MATCH "
    "REGISTRE : si le dossier inclut un match au registre avec un NAF CHR "
    "(restauration 56.xx, débit de boissons, hôtellerie 55.xx, boulangerie/"
    "pâtisserie), surtout société récemment créée, le compte correspond à un "
    "établissement CHR réel — not_venue est alors EXCLU (au pire opening_soon/"
    "just_opened/established selon la fraîcheur). RÈGLE DE DOUTE : "
    "opening_soon et just_opened exigent des "
    "indices EXPLICITES (travaux, compte à rebours, date d'ouverture, 'bientôt', "
    "lancement récent). Sans indice explicite -> unknown ou established, JAMAIS "
    "opening_soon. JUST_OPENED exige une PREUVE EXPLICITE d'ouverture récente "
    "(< 3 mois) : posts de lancement (« nous avons ouvert », « premier jour », "
    "inauguration, « nouvelle adresse » d'un lieu neuf), ou société créée "
    "récemment au registre. Des horaires affichés + un historique de service "
    "SANS preuve de lancement récent = established, PAS just_opened. CHAÎNE : une "
    "nouvelle adresse d'une enseigne qui existe déjà "
    "ailleurs = chain_multisite, même si la création est récente. "
    "l'ÂGE des posts seul ne tranche pas (une pré-ouverture peut teaser "
    "des mois) — ce qui compte est le LONG historique d'EXPLOITATION et l'âge de "
    "la société au registre. RÉOUVERTURE / EXTENSION D'UN EXISTANT : la présence "
    "de posts DATÉS d'il y a ~un an ou plus (saison/année PRÉCÉDENTE), ou une "
    "mention « comme en <année-1> » / « depuis <année> », prouve une exploitation "
    "ANTÉRIEURE : dans ce cas une annonce « ouvre bientôt » / « ouverture de… » "
    "est une RÉOUVERTURE saisonnière (ou l'ouverture d'un nouvel espace/patio/salle "
    "d'une maison existante), donc established (ou chain_multisite si nouveau site), "
    "JAMAIS opening_soon. RÉNOVATION (label renovation, segment CHAUD) : un "
    "établissement à l'apparence ÉTABLIE (ancienneté déclarée, long historique "
    "d'exploitation, horaires/résa) dont les posts RÉCENTS montrent des TRAVAUX "
    "ACTUELLEMENT EN COURS (chantier, « fermé pour travaux », « on refait la "
    "salle », finitions non terminées), OU une réouverture il y a seulement "
    "quelques JOURS avec travaux encore visibles = renovation (la fenêtre "
    "d'aménagement est ouverte MAINTENANT). MAIS un établi qui a DÉJÀ rouvert et "
    "opère normalement (service, carte, horaires) depuis plus de quelques jours — "
    "même si la réouverture est récente (quelques semaines) et même si des posts "
    "évoquent des travaux PASSÉS — a une fenêtre travaux CLOSE = established, PAS "
    "renovation. renovation exige des travaux EN COURS ou tout juste terminés, "
    "jamais un simple souvenir de chantier. "
    "PRIORITÉS QUI TRANCHENT (applique-les AVANT de choisir un label chaud) : "
    "(a) ANCIENNETÉ DÉCLARÉE DOMINE — si la bio déclare une ancienneté (« depuis "
    "1995 », « ouverts depuis <année passée> », « est. 19xx »), le lieu EST "
    "established, quels que soient des posts de réouverture ou un compte clairsemé ; "
    "au MAXIMUM renovation SI un chantier est ACTUELLEMENT en cours, JAMAIS "
    "just_opened ni opening_soon. "
    "(b) RÉOUVERTURE OPÉRATIONNELLE = established — un lieu qui a rouvert et OPÈRE "
    "(posts de service, carte/menu, cocktails, « vous accueille », happy hours, "
    "horaires actifs, « 7j/7 ») a sa fenêtre travaux CLOSE : established, même si la "
    "réouverture date de quelques semaines et même si un long chantier (« après 2 "
    "ans de travaux ») est évoqué. renovation seulement si le lieu est ENCORE fermé "
    "ou en plein chantier. "
    "(c) LONG HISTORIQUE = established (RÈGLE FORTE) — un compte à 100 posts ou "
    "PLUS a un long historique d'exploitation : il ne peut être NI just_opened NI "
    "opening_soon. Il est established (ou chain_multisite si plusieurs adresses ; "
    "ou renovation UNIQUEMENT si le lieu est actuellement FERMÉ / en plein "
    "chantier). Un restaurant à 100+ posts qui poste sa carte, ses plats, ses "
    "horaires (« Ouvert 7j/7 ») ou « vous accueille » OPÈRE — même s'il vient de "
    "rouvrir après un long chantier (« après 2 ans de travaux ») : c'est "
    "established, PAS renovation ni just_opened. "
    "(d) CHAÎNE DOMINE RÉNOVATION — une enseigne à plusieurs adresses (2e/3e "
    "établissement, plusieurs lieux listés, « nouvelle adresse ») = chain_multisite, "
    "même si le NOUVEAU site est en travaux ; chain_multisite l'emporte sur "
    "renovation. "
    "opening_soon et just_opened sont réservés à un lieu SANS "
    "AUCUN passé d'exploitation (aucun post de l'an dernier, société tout juste créée). "
    "En cas de doute sur la fraîcheur -> unknown, JAMAIS "
    "opening_soon par défaut. COMPTE RÉCENT ≠ ÉTABLISSEMENT RÉCENT : un compte "
    "RÉCENT ne signifie PAS un établissement récent — une bio 'nouveau compte', "
    "'ouverts depuis <année>', 'depuis 19xx/20xx' = established, quel que soit "
    "l'âge du compte (ex. 'Nouveau compte, ouverts depuis 1995' = pub établi). "
    "Raisonne D'ABORD brièvement (2 phrases : signaux "
    "d'exploitation, cohérence registre) PUIS décide. Extrais aussi, UNIQUEMENT "
    "depuis la bio/les posts de CE compte, addresses (adresses postales complètes) "
    "et emails. Réponds STRICTEMENT en JSON."
)


def judge_dossier(client, handle: str, name: Optional[str],
                  profile: Dict[str, Any], caption: Optional[str] = None,
                  match_result=None, today: Optional[date] = None) -> Dict[str, Any]:
    """Juge v2 UNITAIRE : un appel LLM par compte sur le dossier complet. Renvoie
    {reasoning, label, confidence, addresses, emails, opening_date} ou {} (fail-
    soft : pas de client / erreur / JSON invalide). Toute arithmétique de dates
    est PRÉCALCULÉE en code (_age_label) — les petits LLM ratent les
    soustractions de dates brutes (leçon des rounds matcher)."""
    if client is None:
        return {}
    today = today or date.today()
    latest = profile.get("latestPosts") or []
    posts_block = "\n".join(
        f'  - {_age_label((x.get("timestamp") or "")[:10], today)} : '
        f'{(x.get("caption") or "")[:180]}'
        for x in latest[:12]
    )
    if match_result is not None:
        registre = (
            f'enseigne={match_result.enseigne or "?"} | NAF={match_result.naf or "?"} '
            f'| société créée {_age_label(match_result.date_creation, today)}'
        )
    else:
        registre = "(aucun match au registre)"
    block = (
        f'@{handle} | {name} | posts={profile.get("postsCount")} '
        f'| abonnés={profile.get("followersCount")} '
        f'| catégorie={profile.get("businessCategoryName")}\n'
        f'bio : {(profile.get("biography") or "")[:250]}\n'
        f'légende de découverte : {(caption or "")[:200]}\n'
        f'registre Sirene : {registre}\n'
        f'derniers posts (âge daté) :\n{posts_block or "  (aucun)"}'
    )
    user = (
        f"Date du jour : {today.isoformat()}\n"
        f"Dossier :\n{block}\n\n"
        'Format EXACT : {"reasoning":"<2 phrases max>","label":"opening_soon|'
        'just_opened|renovation|established|chain_multisite|not_venue|noise|unknown",'
        '"confidence":"haute|moyenne|basse","addresses":[],"emails":[],'
        '"opening_date":"YYYY-MM-DD|null"}'
    )
    try:
        # Modèle DÉDIÉ au juge (variable propre OPENAI_JUDGE_MODEL, défaut gpt-4o) :
        # le juge est la SEULE décision LLM porteuse du funnel (opening/renovation/
        # established…) et gpt-4o-mini est non déterministe à temp 0 sur les profils
        # ambigus (vécu passe 3 : 7 runs, hot_precision 50->64 %). L'arbitre du
        # matcher, la génération de messages et le reste gardent OPENAI_MODEL.
        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_JUDGE_MODEL", "gpt-4o"),
            messages=[{"role": "system", "content": _DOSSIER_SYSTEM},
                      {"role": "user", "content": user}],
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
