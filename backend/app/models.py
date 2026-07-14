"""Modèles SQLModel (tables SQLite)."""
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import Column, DateTime, func
from sqlalchemy.types import JSON
from sqlmodel import Field, Relationship, SQLModel


# --- Constantes métier (utilisées par l'app et la UI) -------------------------

ESTABLISHMENT_TYPES = [
    "restaurant",
    "café",
    "hôtel",
    "bar",
    "traiteur",
    "brasserie",
    "coffee shop",
]

SIGNAL_TYPES = [
    "ouverture prochaine",
    "reprise",
    "rénovation",
    "recrutement",
    "changement propriétaire",
    "nouveau point de vente",
    "travaux visibles",
    "annonce presse locale",
    "création récente",
    "expansion",
    # Signal NEUTRE des leads « en base » (établis/chaînes/indéterminés du funnel
    # Insta) : membre d'AUCUNE famille de scoring -> aucun bonus de nature.
    "établissement en activité",
    # Population ARCHITECTES (A1) : signal NEUTRE des prescripteurs (hors familles
    # de scoring CHR -> aucun bonus de nature) + libellés de tier (bonus ajoutés
    # en T3, jamais émis par les leads CHR -> scores CHR inchangés).
    "prescripteur actif",
    "projet CHR détecté",
    "portfolio hospitality/CHR",
    "studio en sommeil",
]

CHANNELS = ["instagram", "telephone", "email", "linkedin"]

STATUSES = [
    "non_contacte",
    "contacte",
    "relance",
    "interesse",
    "rdv",
    "gagne",
    "perdu",
]

# Types d'activités du journal de suivi de contact (closers Ambient Home).
# SOBRE et fermé : quatre gestes rapides + le journal AUTO des changements de
# statut ('statut'). Distinct des `action_type` libres de ContactHistory (qui
# trace des événements système : message généré, ingestion...).
ACTIVITY_TYPES = ["appel", "email", "dm_insta", "note", "statut"]

# Rôles des comptes (auth légère). 'admin' = le patron (Alexis) : assigne, voit
# le journal global ; 'closer' = commercial qui fait son suivi sur l'app.
USER_ROLES = ["admin", "closer"]

# --- Qualification des contacts (cross-canal) ---------------------------------
# Taxonomie à 3 niveaux posée par-dessus `ContactActivity.type` (le CANAL) :
# `issue` (N1, universel) -> `raison` (N2, par canal) -> `detail` (N3, chips
# libres). Backend = autorité de validation, servie au frontend en lecture (pas
# de duplication de la vérité). Voir docs/plans/2026-07-14-qualification-contacts-design.md.
#
# N1 : « pas joint » (à retenter) et « KO » (impasse) déclenchent des gestes
# opposés -> 3 valeurs, pas 2. Un refus (« pas intéressé ») compte comme JOINT
# (l'échange a eu lieu, c'est une tentative aboutie) ; seul un refus ferme (« ne
# me rappelez plus ») est KO.
QUALIF_ISSUES = ["joint", "pas_joint", "ko"]

# N2 : raisons autorisées par (canal, issue). Clé = tuple (type, issue) — miroir
# de `ACTIVITY_TYPES` pour `type` et de `QUALIF_ISSUES` pour `issue`.
QUALIF_RAISONS = {
    ("appel", "joint"): ["interesse", "a_rappeler", "pas_interesse"],
    ("appel", "pas_joint"): ["repondeur", "pas_de_reponse", "occupe"],
    ("appel", "ko"): ["mauvais_numero", "ferme", "ne_plus_contacter"],
    ("email", "joint"): ["interesse", "a_suivre", "pas_interesse"],
    ("email", "pas_joint"): ["pas_de_reponse"],
    ("email", "ko"): ["bounce", "desinscription"],
    ("dm_insta", "joint"): ["interesse", "a_suivre", "pas_interesse"],
    ("dm_insta", "pas_joint"): ["vu_sans_reponse", "pas_de_reponse"],
    ("dm_insta", "ko"): ["compte_introuvable", "bloque"],
}

# N3 : chips de détail optionnelles, réutilisables sous n'importe quel (canal,
# issue) — surtout pertinentes sous `pas_interesse` / `a_rappeler`.
QUALIF_DETAILS = [
    "deja_fournisseur",
    "pas_de_projet",
    "budget",
    "mauvais_interlocuteur",
    "rappeler_plus_tard",
]


# --- Tables -------------------------------------------------------------------


class Opportunity(SQLModel, table=True):
    __tablename__ = "opportunities"

    id: Optional[int] = Field(default=None, primary_key=True)
    establishment_name: str
    establishment_type: str
    city: str
    address: str

    main_signal: str
    secondary_signals: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    detection_date: date
    # Date de début d'activité (BODACC dateCommencementActivite) : future =>
    # pas encore ouvert (pré-ouverture) ; passée => déjà ouvert. NULL si absent.
    activity_start_date: Optional[date] = None
    # Date d'origine du LOCAL (création du précédent exploitant d'une reprise) :
    # ancienne => lieu établi. NULL si pas une reprise / précédent non résolu.
    venue_origin_date: Optional[date] = None
    estimated_timing: str  # ex: "J-30", "J-60", "J-90"
    probable_needs: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    decision_maker: Optional[str] = None
    # Tous les dirigeants déclarés (Président, DG, Gérant…) — pivots pour joindre
    # le décideur. `decision_maker` = le principal ; ceci = la liste complète.
    dirigeants: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    opportunity_score: int = 0
    score_reason: str = ""
    recommended_channel: str = "telephone"
    channel_reason: str = ""

    proof_text: str = ""
    proof_url: str = ""

    # Provenance : "demo" (seed) ou nom du connecteur (ex: "bodacc").
    source: str = "demo"
    # Référence stable côté source (ex: id d'annonce BODACC) pour la dédup/upsert.
    source_ref: Optional[str] = Field(default=None, index=True)
    # Identifiant entreprise (SIREN) issu de l'enrichissement Sirene.
    siren: Optional[str] = Field(default=None, index=True)
    # Code NAF/APE (rempli par l'enrichissement). NULL = lead non encore
    # validé par Sirene -> cible de la passe de ré-enrichissement.
    naf: Optional[str] = Field(default=None, index=True)
    # SIRET matché (établissement précis) — traçabilité pour la fusion/corroboration.
    siret: Optional[str] = None
    siren_match_method: Optional[str] = None      # nom | adresse | arbitre | source
    siren_match_confidence: Optional[str] = None  # haute | moyenne

    # Étiquette de cycle de vie du funnel Insta (juge/gardes) PERSISTÉE sur la
    # fiche : opening_soon | just_opened | established | chain_multisite | unknown.
    # NULL pour les sources registre (BODACC/Sirene) qui n'étiquettent pas encore.
    lifecycle_label: Optional[str] = Field(default=None, index=True)

    # Population du lead : 'chr' (défaut, toutes les sources registre + funnel CHR)
    # ou 'architecte' (prescripteurs d'archi d'intérieur, A1). Les architectes NE
    # passent PAS par le classifieur CHR ni le juge CHR ; ils ont leur propre
    # découverte/juge/tiering et un main_signal neutre 'prescripteur actif'.
    population: str = Field(default="chr", index=True)

    # Contact (enrichissement gratuit : OSM + scrape de site).
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    # Nb d'abonnés Instagram au moment du scrape (profil Apify) : proxy de
    # taille de compte ("les petits comptes répondent plus souvent"). NULL =
    # inconnu (pas de profil Insta / pas encore scrapé).
    followers_count: Optional[int] = None
    facebook: Optional[str] = None
    # Contacts multiples (ex. profil Insta d'un groupe : plusieurs adresses/emails).
    # Le principal est dans address/email ; ceci = les autres.
    extra_addresses: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    extra_emails: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Nb d'avis Google Places (proxy de fraîcheur : 0-20 = fenêtre d'aménagement
    # ouverte ; 200+ = établissement déjà installé). NULL = inconnu/non matché.
    review_count: Optional[int] = None
    # Confiance du contact ÉTABLISSEMENT (tél/email/site/insta du lieu) :
    # "haute" (match géo) | "moyenne" (nom+ville) | "basse". Pilote l'affichage.
    contact_confidence: Optional[str] = None
    # Bloc DÉCIDEUR : email nominatif de la personne + sa confiance.
    decision_maker_email: Optional[str] = None
    decision_maker_confidence: Optional[str] = None
    # NULL = enrichissement contact pas encore tenté (cible de la passe contact).
    contact_enriched_at: Optional[datetime] = None
    # Refresh : dernière vérification (heartbeat de fraîcheur) et date de fermeture
    # détectée (Sirene état != A) => stage "fermé".
    last_checked_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    status: str = "non_contacte"

    generated_instagram_dm: Optional[str] = None
    generated_email: Optional[str] = None
    generated_linkedin: Optional[str] = None
    generated_call_script: Optional[str] = None

    next_follow_up_date: Optional[date] = None
    # Prochaine action (texte court) associée à `next_follow_up_date` : « UNE
    # prochaine action par fiche ». NULL = aucune action planifiée. Les deux se
    # posent/s'effacent ensemble via PUT /api/opportunities/{id}/next-action.
    next_action: Optional[str] = None

    # Closer assigné (nom d'un User) : le patron (admin) répartit ses leads. NULL
    # = non assigné (filtre « Non assignés » du patron). Posé via PATCH
    # /api/opportunities/{id}/assignment. Stocke le NOM (pas l'id) pour rester
    # lisible et aligné sur `ContactActivity.author`.
    assigned_to: Optional[str] = Field(default=None, index=True)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=func.now(), onupdate=func.now()),
    )

    signals: List["Signal"] = Relationship(
        back_populates="opportunity",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    contact_history: List["ContactHistory"] = Relationship(
        back_populates="opportunity",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "ContactHistory.created_at.desc()",
        },
    )
    contact_activities: List["ContactActivity"] = Relationship(
        back_populates="opportunity",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "ContactActivity.created_at.desc()",
        },
    )


class Signal(SQLModel, table=True):
    __tablename__ = "signals"

    id: Optional[int] = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunities.id")
    signal_type: str
    source: str = ""
    source_url: str = ""
    signal_date: date
    confidence_score: float = 0.5
    raw_text: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    opportunity: Optional[Opportunity] = Relationship(back_populates="signals")


class ContactHistory(SQLModel, table=True):
    __tablename__ = "contact_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunities.id")
    channel: Optional[str] = None
    message: Optional[str] = None
    action_type: str  # ex: "message_genere", "statut_change", "relance_planifiee"
    status: Optional[str] = None
    note: Optional[str] = None
    contacted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    opportunity: Optional[Opportunity] = Relationship(back_populates="contact_history")


class ContactActivity(SQLModel, table=True):
    """Journal d'activités SOBRE par fiche (suivi de contact des closers) :
    quoi (`type`) / quand (`created_at`), avec une note optionnelle. Alimenté par
    les gestes rapides (« J'ai appelé », « Email envoyé », « DM envoyé », « Note »)
    et par le journal AUTO des changements de statut (type 'statut', note
    « ancien -> nouveau »). Volontairement distinct de ContactHistory pour ne pas
    mêler le suivi commercial aux événements système (messages IA, ingestion)."""
    __tablename__ = "contact_activities"

    id: Optional[int] = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunities.id", index=True)
    type: str  # appel | email | dm_insta | note | statut (ACTIVITY_TYPES)
    note: Optional[str] = None
    # Auteur de l'activité (closer Ambient Home). Optionnel pour l'instant :
    # l'authentification viendra plus tard, mais la colonne existe AVANT que les
    # données ne s'accumulent (fondation des comptes closers). NULL = inconnu.
    author: Optional[str] = None
    # Qualification cross-canal (N1/N2/N3, cf. QUALIF_ISSUES/QUALIF_RAISONS/
    # QUALIF_DETAILS) : NE MODIFIE JAMAIS la fiche (statut/flags) — enregistrée et
    # agrégée pour le monitoring UNIQUEMENT. NULL = pas de résultat encore connu
    # (ex. « Email envoyé » : action d'émission sans résultat).
    issue: Optional[str] = None  # N1 : joint | pas_joint | ko
    raison: Optional[str] = None  # N2 : cf. QUALIF_RAISONS[(type, issue)]
    detail: List[str] = Field(default_factory=list, sa_column=Column(JSON))  # N3
    created_at: datetime = Field(default_factory=datetime.utcnow)

    opportunity: Optional[Opportunity] = Relationship(back_populates="contact_activities")


class User(SQLModel, table=True):
    """Compte (auth légère) : le patron (admin) et ses closers. Table NEUVE :
    créée par `SQLModel.metadata.create_all` (rien à ajouter aux migrations
    légères). Le mot de passe n'est JAMAIS stocké en clair (bcrypt)."""
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str = Field(index=True, unique=True)
    password_hash: str
    role: str = "closer"  # 'admin' | 'closer' (USER_ROLES)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Settings(SQLModel, table=True):
    __tablename__ = "settings"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider_name: str = "LumaPro"
    provider_offer: str = (
        "luminaires, mobilier et solutions d'ambiance pour restaurants, "
        "hôtels et commerces"
    )
    tone: str = "professionnel, direct, personnalisé"
    target_area: str = "Île-de-France"
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=func.now(), onupdate=func.now()),
    )


class HandleVerdict(SQLModel, table=True):
    """Cache des verdicts du funnel Insta v2 (brique 3). Dans le flux hashtag
    actuel, un handle n'est re-scrapé/re-jugé que si `now > revisit_after` (seule
    la fenêtre temporelle décide : should_rejudge est appelé avant le scrape, sans
    profil). L'invalidation par `profile_hash` (« si le profil a changé ») est
    ÉCRITE mais pas encore exercée — réservée à la revisite périodique de la brique
    4. Les `opening_soon`/`just_opened` ne sont jamais mis en sommeil
    (`revisit_after=None`) : ils restent sur la watchlist."""
    __tablename__ = "handle_verdicts"

    id: Optional[int] = Field(default=None, primary_key=True)
    handle: str = Field(index=True, unique=True)
    verdict: str
    confidence: Optional[str] = None
    judged_at: datetime = Field(default_factory=datetime.utcnow)
    # Date à partir de laquelle re-juger. NULL = jamais mis en sommeil (watchlist).
    revisit_after: Optional[date] = None
    # sha1(biography + postsCount). Destiné à re-juger hors fenêtre si le profil
    # change — chemin réservé à la brique 4 (non déclenché par le flux actuel).
    profile_hash: str = ""
