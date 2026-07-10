"""Garde-fous déterministes du profil Insta (brique 3, avant tout LLM).

Fonctions PURES : à partir d'un profil brut (sortie du profile scraper Apify),
renvoient un verdict déterministe (`established`/`chain_multisite`/`noise`) ou None si
aucun signal certain — le compte descend alors au juge LLM (`judge_dossier`).
Gratuit et reproductible : attrape l'évident (chaînes multi-adresses, gros
volume de posts, historique long, horaires/résa affichés) sans dépenser de
crédit LLM. Cas de non-régression : MOKA (3 adresses en bio + 'open everyday')
meurt ici en chain_multisite.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, List, Optional

# Volume de posts au-delà duquel un compte est clairement établi (Le Palais=200).
POSTS_ESTABLISHED_HARD = 150

# Hébergeurs de réservation en ligne = établissement en exploitation.
_RESA_HOSTS = ("zenchef", "thefork", "lafourchette", "sevenrooms", "opentable",
               "resy", "newtable")

# Réservation : mot-clé (normalisé, sans accent) + téléphone FR / URL = en service.
_RESA_KW = "reserv"  # réservation / réserver / réservez
_PHONE_RE = re.compile(r"\b0\s?\d(?:[\s.\-]?\d\d){4}\b")   # 01 43 25 87 99, 0143258799…
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

# Indices de PRÉ-OUVERTURE (normalisés, sans accent). Leur présence en bio OU
# dans une légende récente INTERDIT tout verdict « established » tiré d'une
# simple mention de réservation : une pré-ouverture ouvre souvent la résa en
# ligne AVANT d'ouvrir ses portes (ex. villa.henriette « Ouverture 10 Juillet »,
# « OPENING SOON »). Sans ce garde, un vrai lead `opening_soon` serait tué au
# garde avant d'atteindre le juge — régression de rappel sur le signal privilégié.
_OPENING_CUES = ("ouverture", "on ouvre", "ouvre bientot", "opening soon",
                 "openingsoon", "coming soon", "comingsoon", "bientot",
                 "prochainement")

# Villes connues (multi-sites en bio). Volontairement des grandes villes non
# ambiguës — évite de compter un simple gentilé comme une 2e adresse.
_CITY_TOKENS = ("paris", "lyon", "marseille", "bordeaux", "lille", "toulouse",
                "nantes", "nice", "strasbourg", "montpellier", "rennes", "cannes")

# Horaires : plages "10h-18h" / "10:30-23:00" / "10h30-23h00" / "de 10h à 19h".
# On N'ACCEPTE PAS un "Nh" isolé (ex. "ouverture dans 48h" = compte à rebours de
# pré-ouverture, PAS des horaires) : seuls les mots-clés et les VRAIES plages
# comptent (leçon de revue : le bare-\d{1,2}h sur-étiquetait des pré-ouvertures).
_RANGE_RE = re.compile(r"\d{1,2}\s?[:h]\s?\d{0,2}\s*[-–—à]\s*\d{1,2}\s?[:h]\s?\d{0,2}")
_HOURS_KW = (
    "ouvert du", "ouvert 7", "ouvert tous les jours", "tous les jours",
    "open everyday", "open every day", "7j/7", "7/7", "midi et soir",
)
_POSTAL_RE = re.compile(r"\b\d{5}\b")
_PIN = "\U0001F4CD"  # 📍

# --- Apparence de lieu CHR (garde-fou des verdicts established/chain_multisite) --
# Motif d'erreur mesuré (passe d'annotation #1) : de longs historiques / des
# horaires promouvaient en « established » des NON-LIEUX (photographes, médias,
# agences, marques hors-secteur). Les gardes established/chain_multisite ne se
# déclenchent donc plus que si le compte a l'APPARENCE d'un lieu CHR. Sinon le
# compte descend au juge LLM (qui sait dire not_venue ; verdict caché 12 mois,
# coût LLM unique).

# Catégorie business Instagram qui dénote un NON-LIEU (prestataire, média,
# marque produit) : DISQUALIFIE d'emblée, même si la bio parle de restos
# (un photographe qui shoote des cafés n'est pas un café). Normalisée, sans accent.
_NON_VENUE_CATEGORIES = (
    "photographer", "photography", "reference website", "product/service",
    "blogger", "personal blog", "content creator", "magazine", "media",
    "journalist", "graphic designer",
)

# Indices de NON-LIEU en bio / nom (quand la catégorie business est vide) :
# prestataire de contenu / média / photographe. Un compte qui parle de
# restaurants comme SUJET (« création de contenu pour restaurants ») n'est pas
# lui-même un restaurant. Normalisés, sans accent.
_NON_VENUE_BIO_CUES = (
    "creation de contenu", "createur de contenu", "creatrice de contenu",
    "content creator", "community manager", "photographe", "webmagazine",
)

# Mots de voie = adresse postale déclarée -> lieu physique (avec le n° de rue).
_STREET_KW = (
    "rue ", "avenue ", "boulevard ", " bd ", "place ", "quai ", "chemin ",
    "impasse ", "cours ", "route ", "allee ", "passage ", "promenade ",
)

# Catégorie business Instagram qui dénote un LIEU CHR (accueille du public).
# Volontairement SPÉCIFIQUE (pas le générique « food & beverage », qui couvre
# aussi les boucheries/épiceries hors-CHR) : chaque terme est un type de salle.
_VENUE_CATEGORIES = (
    "restaurant", "cafe", "coffee", "brasserie", "bistro", "pub", "pizzeria",
    "creperie", "tea room", "salon de the", "bakery", "boulangerie", "hotel",
    "wine bar", "cocktail", "diner", "bar a vin", "glacier", "gelateria",
)

# Mots-clés CHR reconnus DANS la bio / le fullName. Sous-ensemble VOLONTAIREMENT
# plus étroit que instagram.CHR_KEYWORDS : on écarte les termes d'artisanat /
# vente à emporter / trop génériques (« traiteur », « food », « boulangerie »,
# « patisserie », « snack ») qui sur-captent des non-lieux (ex. la boucherie-
# traiteur maisonsaintaubain). On ne garde que des types de salle sans ambiguïté.
_VENUE_KEYWORDS = (
    "restaurant", "resto", "brasserie", "bistrot", "bistro", "pizzeria",
    "trattoria", "creperie", "cafe de specialite", "coffee shop", "coffeeshop",
    "salon de the", "cave a vin", "bar a vin", "gastronomie", "gastronomique",
)

# Mois / saisons / année : un segment de ligne pin qui est une DATE d'ouverture
# ("Juillet 2026", "Printemps/Été 2026") n'est PAS une 2e adresse.
_MONTHS = ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
           "aout", "septembre", "octobre", "novembre", "decembre")
_SEASONS = ("printemps", "ete", "automne", "hiver")
_YEAR_RE = re.compile(r"\b20\d\d\b")

# Ancienneté DÉCLARÉE en bio (« Ouverts depuis 1995 », « depuis 2003 »,
# « établis depuis 1998 », « est. 1974 ») : un établissement qui affiche son année
# d'ouverture EST established, quel que soit l'âge du COMPTE Instagram (piège
# documenté : « Nouveau compte, ouverts depuis 1995 » = pub de 30 ans au compte
# neuf, sorti à tort opening_soon en prod). La regex ancre la mention d'ancienneté
# (ouvert…/depuis/établi depuis/est.) IMMÉDIATEMENT devant l'année ; le filtre
# « année < année courante » exclut toute date d'OUVERTURE FUTURE (« depuis 2026 »,
# « Ouverture Juillet 2026 »). Texte normalisé (sans accent) attendu en entrée.
_SENIORITY_RE = re.compile(
    r"(?:ouvert(?:e?s?)|depuis|etabli(?:e?s?)\s+depuis|est\.?)"
    r"\s*(?:depuis\s*)?"
    r"((?:19|20)\d{2})"
)


def _norm(text: Optional[str]) -> str:
    text = (text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def _has_hours_in_bio(bio: Optional[str]) -> bool:
    """Horaires d'ouverture affichés = lieu en exploitation. Mots-clés OU vraie
    plage horaire uniquement — un "Nh" isolé (compte à rebours de pré-ouverture,
    ex. "ouverture dans 48h") ne compte PAS (évite un faux `established`)."""
    if not bio:
        return False
    t = _norm(bio)
    if any(kw in t for kw in _HOURS_KW):
        return True
    return bool(_RANGE_RE.search(bio))


def _has_reservation_link(profile: Dict[str, Any]) -> bool:
    """Lien de réservation (bio ou externalUrl(s)) = établissement en service."""
    parts: List[str] = [
        profile.get("externalUrl") or "",
        profile.get("biography") or "",
    ]
    for e in profile.get("externalUrls") or []:
        parts.append((e.get("url") or ""))
    hay = " ".join(parts).lower()
    return any(host in hay for host in _RESA_HOSTS)


def _has_reservation_in_bio(bio: Optional[str]) -> bool:
    """Réservation active DANS LA BIO : mot-clé 'réserv…' + numéro de téléphone.
    Signal fort d'exploitation (une pré-ouverture n'affiche pas de ligne de résa).
    Cas ancré : osabaita ('Réservation : 01 43 25 87 99')."""
    if not bio:
        return False
    return _RESA_KW in _norm(bio) and bool(_PHONE_RE.search(bio))


def _has_opening_cue(profile: Dict[str, Any]) -> bool:
    """True si la bio OU une des ~12 dernières légendes annonce une (pré-)ouverture.
    Sert de veto au verdict `established` déterministe tiré d'une résa (une résa
    en ligne peut être teasée avant l'ouverture)."""
    texts = [profile.get("biography") or ""]
    texts += [(x.get("caption") or "") for x in (profile.get("latestPosts") or [])[:12]]
    joined = _norm(" \n ".join(texts))
    return any(cue in joined for cue in _OPENING_CUES)


def _is_dead_account(profile: Dict[str, Any]) -> bool:
    """Compte quasi mort = BRUIT certain : très peu de posts (<=3) ET quasi aucun
    abonné (<=10) ET bio vide/quasi-vide, ET AUCUN indice de (pré-)ouverture.
    Cas ancré : chickntikka94 (2 posts / 1 abonné / pas de bio).

    Le veto `_has_opening_cue` est IMPÉRATIF (ne PAS le retirer) : une pré-ouverture
    naissante a souvent peu de posts et peu d'abonnés MAIS annonce son ouverture —
    loumasrestaurant (2 posts, bio « ouverture prochainement ») et tregusto
    (captions d'ouverture) ne doivent JAMAIS être écrasés en noise, sinon perte
    d'un vrai lead `opening` (garde-fou absolu « recall opening »)."""
    posts = profile.get("postsCount")
    followers = profile.get("followersCount")
    if not isinstance(posts, int) or not isinstance(followers, int):
        return False
    if posts > 3 or followers > 10:
        return False
    if len(_norm(profile.get("biography") or "").strip()) > 5:
        return False
    return not _has_opening_cue(profile)


def _has_reservation_in_posts(profile: Dict[str, Any]) -> bool:
    """Un post récent appelle à RÉSERVER via un site (réserv… + URL) ET le profil
    ne porte AUCUN indice de pré-ouverture (bio/légendes, cf. `_has_opening_cue`)
    = établissement DÉJÀ en service.

    Le veto pré-ouverture est IMPÉRATIF (ne PAS le retirer) : une pré-ouverture
    ouvre fréquemment la réservation en ligne avant d'ouvrir ses portes ; sans ce
    garde, ce helper capturerait un vrai `opening_soon` et le tuerait AVANT le
    juge — l'exact opposé du garde-fou absolu « recall opening ».

    NB : villa.henriette_cabourg — le cas qui avait motivé ce helper — est en
    réalité une PRÉ-OUVERTURE (bio « Ouverture 10 Juillet 2026 », « OPENING
    SOON », post « réservations sur notre site www.villa-henriette.fr »). Elle
    n'est donc **volontairement PAS** captée ici et retombe au juge. Ce helper ne
    capte plus que de vraies résas d'établissements déjà ouverts (test synthétique
    + régression de non-capture d'une pré-ouverture)."""
    if _has_opening_cue(profile):
        return False
    for x in (profile.get("latestPosts") or [])[:12]:
        cap = x.get("caption") or ""
        if _RESA_KW in _norm(cap) and _URL_RE.search(cap):
            return True
    return False


def _declares_seniority(profile: Dict[str, Any], today: date) -> bool:
    """True si la BIO déclare une année d'ouverture PASSÉE (« Ouverts depuis 1995 »,
    « depuis 2003 », « est. 1998 ») -> établissement établi, quel que soit l'âge du
    COMPTE. Deux garde-fous contre une date d'OUVERTURE FUTURE prise à tort :
      - l'année capturée doit être STRICTEMENT antérieure à l'année courante
        (« depuis 2026 », l'année du jour, ne compte pas) ;
      - VETO « pré-ouverture datée » : si la BIO combine un indice de (pré-)ouverture
        (`_OPENING_CUES`) ET une année ≥ année courante, on retombe au juge — c'est
        une ouverture FUTURE (« depuis 2019 on en rêvait, ouverture 2026 »), pas une
        ancienneté. Le veto est SCOPÉ À LA BIO et exige une DATE future : on n'utilise
        PAS `_has_opening_cue` (qui scanne les légendes) car « réouverture » saisonnière
        y matche « ouverture » — or une réouverture est justement un signal d'ÉTABLI
        (cas ancré shywawapub : posts « réouverture », mais bio « ouverts depuis 1995 »).
    Cas ancré : shywawapub (« Nouveau compte / Ouverts depuis 1995 ») -> established."""
    bio_norm = _norm(profile.get("biography"))
    has_future_year = any(int(y) >= today.year for y in _YEAR_RE.findall(bio_norm))
    if has_future_year and any(cue in bio_norm for cue in _OPENING_CUES):
        return False
    for m in _SENIORITY_RE.finditer(bio_norm):
        if int(m.group(1)) < today.year:
            return True
    return False


def _multi_city_in_bio(bio: Optional[str]) -> bool:
    """≥2 villes connues distinctes listées sur une MÊME ligne de bio (séparateur
    virgule / pipe / •) = marque multi-sites. Cas ancré : cherescousinesbagels
    ('Lyon 6, Paris 11'). Restreint aux lignes EN LISTE pour éviter les faux
    positifs (une phrase mentionnant deux villes n'est pas une liste d'adresses)."""
    if not bio:
        return False
    for line in bio.splitlines():
        if not any(sep in line for sep in (",", "|", "•")):
            continue
        t = _norm(line)
        cities = {c for c in _CITY_TOKENS if re.search(r"\b" + c + r"\b", t)}
        if len(cities) >= 2:
            return True
    return False


def _is_date_segment(seg: str) -> bool:
    """True si un segment de ligne pin est une DATE d'ouverture (mois, saison ou
    année) plutôt qu'une adresse — ex. 'Juillet 2026', 'Printemps/Été 2026'.
    Évite de compter un mois d'ouverture comme une 2e adresse (faux
    chain_multisite : cas chezgratien '📍 Villeneuve d'Aveyron | Juillet 2026')."""
    if _YEAR_RE.search(seg):
        return True
    t = _norm(seg)
    return any(tok in t for tok in _MONTHS + _SEASONS)


def _count_addresses_in_bio(bio: Optional[str]) -> int:
    """Nombre d'adresses distinctes déclarées en bio. Deux signaux :
    - codes postaux distincts (\\b\\d{5}\\b) ;
    - liste de lieux marquée par un pin 📍 et séparée par | ou • (cas MOKA :
      '📍Champs Elysées | Opéra | Galeries Lafayette' -> 3). Les segments qui sont
      une date d'ouverture (mois/saison/année) sont EXCLUS : une ligne pin comme
      '📍 Villeneuve d'Aveyron | Juillet 2026' compte 1 adresse, pas 2.
    Renvoie le maximum des deux comptes."""
    if not bio:
        return 0
    postals = len(set(_POSTAL_RE.findall(bio)))
    pin_max = 0
    for line in bio.splitlines():
        if _PIN in line:
            segs = [s for s in re.split(r"[|•]", line.replace(_PIN, ""))
                    if s.strip() and not _is_date_segment(s)]
            pin_max = max(pin_max, len(segs))
    return max(postals, pin_max)


def _long_history(profile: Dict[str, Any], today: date, threshold_days: int = 150) -> bool:
    """True si l'exploitation dure depuis des mois (plusieurs posts anciens) =>
    établi. Reprend la logique de _profile_long_history (migrée depuis
    instagram.py). Robuste : exige PLUSIEURS posts vieux (pas un throwback isolé)."""
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


def _looks_like_venue(profile: Dict[str, Any]) -> bool:
    """True si le compte a l'APPARENCE d'un lieu CHR (café, restaurant, bar,
    hôtel, brasserie…). Garde-fou PUR : conditionne les verdicts déterministes
    established/chain_multisite pour ne PLUS promouvoir des non-lieux (prestataires,
    médias, agences, marques hors-secteur) qui affichent un long historique ou des
    horaires. Un compte sans apparence de lieu -> le juge LLM tranche (il sait dire
    not_venue). Signaux, dans l'ordre :
      1. catégorie / bio de NON-LIEU (photographe, média, prestataire) -> False, quoi qu'il arrive ;
      2. catégorie business CHR explicite (type de salle) -> True ;
      3. mot-clé CHR (type de salle) en bio ou fullName -> True ;
      4. adresse postale déclarée en bio (code postal ou n° + voie) -> True.
    """
    cat = _norm(profile.get("businessCategoryName"))
    hay = _norm(profile.get("biography")) + " \n " + _norm(profile.get("fullName"))
    # 1. Non-lieu certain (catégorie ou bio) : ni la bio « restos », ni un long
    # historique ne doivent le promouvoir en lieu.
    if any(c in cat for c in _NON_VENUE_CATEGORIES):
        return False
    if any(c in hay for c in _NON_VENUE_BIO_CUES):
        return False
    # 2. Catégorie business = type de salle CHR.
    if any(c in cat for c in _VENUE_CATEGORIES):
        return True
    # 3. Mot-clé « type de salle » en bio / nom.
    if any(k in hay for k in _VENUE_KEYWORDS):
        return True
    # 4. Adresse postale déclarée (code postal, ou n° de rue + voie).
    bio_norm = _norm(profile.get("biography"))
    if _POSTAL_RE.search(profile.get("biography") or ""):
        return True
    if any(kw in bio_norm for kw in _STREET_KW) and re.search(r"\d", bio_norm):
        return True
    return False


def guard_verdict(profile: Dict[str, Any], today: Optional[date] = None) -> Optional[str]:
    """Verdict déterministe du profil, ou None (à confier au juge LLM).
    Ordre : compte-mort -> noise ; puis — UNIQUEMENT si le compte a l'apparence
    d'un lieu CHR (_looks_like_venue) — multi-adresses / multi-villes ->
    chain_multisite, sinon volume / historique / horaires / résa -> established ;
    sinon None.

    Le garde `_looks_like_venue` corrige le motif d'erreur #1 (passe d'annotation) :
    des non-lieux (photographes, médias, agences) au long historique étaient
    promus « established » et n'atteignaient jamais le juge. Désormais ils
    descendent au juge (not_venue), à coût LLM unique (verdict caché 12 mois)."""
    today = today or date.today()
    bio = profile.get("biography") or ""
    # Garde compte-mort AVANT tout : bruit certain (peu de posts + quasi zéro
    # abonné + pas de bio + aucun indice d'ouverture). Route « noise » (pas de
    # lead, cache 2 mois) sans dépenser le juge.
    if _is_dead_account(profile):
        return "noise"
    # Gardes established/chain_multisite : conditionnés à l'apparence de lieu CHR.
    # Sinon -> None (le juge tranche : not_venue pour un prestataire/média).
    if not _looks_like_venue(profile):
        return None
    if _count_addresses_in_bio(bio) >= 2 or _multi_city_in_bio(bio):
        return "chain_multisite"
    # Ancienneté déclarée (« ouverts depuis <année passée> ») : établi, même si le
    # COMPTE Insta est neuf (piège « nouveau compte, ouverts depuis 1995 »).
    if _declares_seniority(profile, today):
        return "established"
    posts_count = profile.get("postsCount")
    if isinstance(posts_count, int) and posts_count > POSTS_ESTABLISHED_HARD:
        return "established"
    if _long_history(profile, today):
        return "established"
    if _has_hours_in_bio(bio):
        return "established"
    if _has_reservation_link(profile):
        return "established"
    if _has_reservation_in_bio(bio):
        return "established"
    if _has_reservation_in_posts(profile):
        return "established"
    return None
