"use client";

// Suivi de contact SOBRE (critère d'acceptation « pas le fouilli ») : barre de
// qualification cross-canal + journal d'activités compact + une prochaine
// action par fiche. Regroupé ici pour rester réutilisable et garder page.tsx
// lisible. Cf. docs/plans/2026-07-14-qualification-contacts-design.md.

import { useEffect, useRef, useState } from "react";
import {
  Phone,
  PhoneCall,
  Mail,
  Instagram,
  StickyNote,
  ArrowRightLeft,
  Loader2,
  X,
  Plus,
  UserCog,
  Trophy,
  ArrowRight,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ContactActivity, PhoneCandidate, QualifTaxonomy, UserPublic } from "@/lib/types";
import { useAuth } from "@/lib/auth";
import {
  ACTIVITY_TYPE_LABELS,
  QUALIF_ISSUE_BUTTON_STYLES,
  QUALIF_ISSUE_LABELS,
  QUALIF_RAISON_LABELS,
  QUALIF_RAISON_HERO,
  QUALIF_DETAIL_LABELS,
  STATUS_LABELS,
  formatDate,
  formatRelativeDate,
  isOverdue,
  recommendedToActivityType,
} from "@/lib/labels";
import { IssueBadge } from "@/components/Badges";

// Note d'un changement de statut auto-journalisé : "ancien -> nouveau" (clés
// techniques) rendu en libellés FR pour le closer ("Non contacté → Contacté").
function frStatusNote(note: string): string {
  const parts = note.split("->").map((s) => s.trim());
  if (parts.length !== 2) return note;
  return `${STATUS_LABELS[parts[0]] ?? parts[0]} → ${STATUS_LABELS[parts[1]] ?? parts[1]}`;
}

const ACTIVITY_ICONS: Record<string, typeof Phone> = {
  appel: Phone,
  email: Mail,
  dm_insta: Instagram,
  note: StickyNote,
  statut: ArrowRightLeft,
};

const FOLD_AT = 5;

const CANAL_OPTIONS: { type: "appel" | "email" | "dm_insta"; label: string; icon: typeof Phone }[] = [
  { type: "appel", label: "Appel", icon: Phone },
  { type: "email", label: "Email", icon: Mail },
  { type: "dm_insta", label: "DM", icon: Instagram },
];

function issueLabelClass(issue: string): string {
  if (issue === "joint") return "text-emerald-600";
  if (issue === "pas_joint") return "text-amber-600";
  return "text-rose-600";
}

// --- Barre de qualification (canal-aware, N1/N2/N3) --------------------------
// Remplace l'ancien QuickActions : chemin rapide = 1 tap = 1 POST (couleur =
// lecture immédiate) ; « + détail » = chips N3 + note, opt-in, même POST.

export function QualificationBar({
  opportunityId,
  recommendedChannel,
  phone,
  phoneCandidates,
  email,
  extraEmails,
  instagram,
  onAdded,
  onMarkRdv,
}: {
  opportunityId: number;
  recommendedChannel?: string | null;
  // Contact tenté (§5.2) : options du dropdown par canal — le principal + les
  // candidats/extras. Tous optionnels (fiches partielles).
  phone?: string | null;
  phoneCandidates?: PhoneCandidate[];
  email?: string | null;
  extraEmails?: string[];
  instagram?: string | null;
  onAdded: () => void;
  // Qualifier « RDV pris » (raison hero) ne change JAMAIS le statut tout seul
  // (invariant « on monitore, on ne nourrit pas » — routes/activities.py) :
  // ce callback optionnel n'est déclenché QUE par un clic explicite du closer
  // sur la suggestion ci-dessous, jamais automatiquement (revue produit §2).
  onMarkRdv?: () => void;
}) {
  const [taxonomy, setTaxonomy] = useState<QualifTaxonomy | null>(null);
  const [canal, setCanal] = useState<"appel" | "email" | "dm_insta">(
    recommendedToActivityType(recommendedChannel)
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [selected, setSelected] = useState<{ issue: string; raison: string } | null>(null);
  const [chips, setChips] = useState<string[]>([]);
  const [detailNote, setDetailNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteText, setNoteText] = useState("");
  // Dernier tap rapide (chemin 1-clic) : permet de rattacher un détail (N3) à
  // CETTE activité via PATCH au lieu de reposter une 2ᵉ ligne si le closer
  // rouvre « + Détail » juste après (cf. revue produit — ordre issue-d'abord).
  const [justPosted, setJustPosted] = useState<{ id: number; issue: string; raison: string } | null>(null);
  // Contact EFFECTIVEMENT tenté (§3/§5.2) : préaffiché sur le contact du canal
  // courant, modifiable. `null` = pas d'override -> valeur par défaut du canal.
  const [contactUsedOverride, setContactUsedOverride] = useState<string | null>(null);
  // Suggestion visuelle post « Mauvais numéro » (§5.2) : AUCUNE mutation, juste
  // une présélection du candidat suivant pour le prochain geste.
  const [suggestion, setSuggestion] = useState<string | null>(null);
  // Suggestion visuelle post « RDV pris » (raison hero) : rappelle qu'un clic
  // séparé reste nécessaire pour marquer le statut de la fiche (revue produit
  // §2 — qualification et statut sont deux systèmes volontairement découplés).
  // AUCUNE mutation tant que le closer n'a pas cliqué le bouton.
  const [heroSuggestion, setHeroSuggestion] = useState(false);

  useEffect(() => {
    api
      .getMetaCached()
      .then((m) => setTaxonomy(m.qualif_taxonomy))
      .catch(() => {});
  }, []);

  const phoneOptions = [phone, ...(phoneCandidates ?? []).map((c) => c.number)].filter(
    (v): v is string => Boolean(v)
  );
  const emailOptions = [email, ...(extraEmails ?? [])].filter((v): v is string => Boolean(v));
  const instaHandle = instagram ? `@${instagram.replace(/^@/, "")}` : null;

  const defaultContactUsed = (): string | undefined => {
    if (canal === "appel") return phoneOptions[0];
    if (canal === "email") return emailOptions[0];
    return instaHandle ?? undefined;
  };
  const contactUsed = contactUsedOverride ?? defaultContactUsed();
  const contactOptions = canal === "appel" ? phoneOptions : canal === "email" ? emailOptions : [];

  const post = async (
    body: {
      type: string;
      issue?: string;
      raison?: string;
      detail?: string[];
      note?: string;
      contact_used?: string;
    },
    key: string
  ) => {
    setBusy(key);
    try {
      const activity = await api.addActivity(opportunityId, body);
      onAdded();
      setDetailOpen(false);
      setSelected(null);
      setChips([]);
      setDetailNote("");
      return activity;
    } finally {
      setBusy(null);
    }
  };

  // « Mauvais numéro » (appel uniquement) : suggère le candidat suivant, SANS
  // rien promouvoir ni muter — juste une présélection pour le prochain geste.
  const maybeSuggestNext = (issue: string, raison: string, usedContact?: string) => {
    if (canal === "appel" && issue === "ko" && raison === "mauvais_numero") {
      const remaining = phoneOptions.filter((n) => n !== usedContact);
      setSuggestion(remaining[0] ?? null);
    } else {
      setSuggestion(null);
    }
    // Tout canal : la raison hero (« RDV pris ») déclenche la suggestion de
    // statut, jamais une mutation directe.
    setHeroSuggestion(issue === "joint" && raison === QUALIF_RAISON_HERO);
  };

  // Chemin rapide (défaut) : chaque chip = 1 POST immédiat — on retient
  // l'activité créée (`justPosted`) pour pouvoir y rattacher un détail après
  // coup (voir « + détail » plus bas) sans reposter une 2ᵉ ligne. Chemin
  // détaillé (« + détail » déjà ouvert) : la chip sélectionne seulement, la
  // validation se fait plus bas avec les chips N3 + la note (un seul POST).
  const pick = async (issue: string, raison: string) => {
    if (!detailOpen) {
      const usedContact = contactUsed;
      const activity = await post(
        { type: canal, issue, raison, contact_used: usedContact },
        `${issue}-${raison}`
      );
      if (activity) setJustPosted({ id: activity.id, issue, raison });
      maybeSuggestNext(issue, raison, usedContact);
    } else {
      setSelected({ issue, raison });
      setJustPosted(null); // nouvelle sélection explicite -> plus de lien avec un post précédent
    }
  };

  // Si le closer rouvre « + Détail » juste après un tap rapide sans avoir
  // changé de case, on précharge sa dernière qualification : `submitDetailed`
  // enrichira alors CETTE activité (PATCH) plutôt que d'en créer une nouvelle.
  const toggleDetail = () => {
    setDetailOpen((v) => {
      const next = !v;
      if (next && justPosted && !selected) setSelected(justPosted);
      return next;
    });
  };

  const isEnrichingJustPosted = Boolean(
    selected && justPosted && selected.issue === justPosted.issue && selected.raison === justPosted.raison
  );

  const submitDetailed = async () => {
    if (!selected) return;
    if (isEnrichingJustPosted && justPosted) {
      setBusy("detail");
      try {
        await api.updateActivityDetail(opportunityId, justPosted.id, {
          detail: chips,
          note: detailNote.trim() || undefined,
        });
        onAdded();
        setDetailOpen(false);
        setSelected(null);
        setChips([]);
        setDetailNote("");
        setJustPosted(null);
      } finally {
        setBusy(null);
      }
      return;
    }
    const usedContact = contactUsed;
    await post(
      {
        type: canal,
        issue: selected.issue,
        raison: selected.raison,
        detail: chips,
        note: detailNote.trim() || undefined,
        contact_used: usedContact,
      },
      "detail"
    );
    maybeSuggestNext(selected.issue, selected.raison, usedContact);
  };

  const emit = async () => {
    setBusy("emit");
    try {
      await api.addActivity(opportunityId, { type: canal, contact_used: contactUsed });
      onAdded();
    } finally {
      setBusy(null);
    }
  };

  const submitNote = async () => {
    const note = noteText.trim();
    if (!note) return;
    setBusy("note");
    try {
      await api.addActivity(opportunityId, { type: "note", note });
      onAdded();
      setNoteText("");
      setNoteOpen(false);
    } finally {
      setBusy(null);
    }
  };

  if (!taxonomy) {
    return <p className="text-sm text-slate-400">Chargement…</p>;
  }

  const raisonsByIssue = taxonomy.raisons[canal] ?? {};

  return (
    <div>
      {/* Canal */}
      <div className="mb-3 inline-flex rounded-lg border border-slate-200 p-0.5">
        {CANAL_OPTIONS.map((c) => (
          <button
            key={c.type}
            onClick={() => {
              setCanal(c.type);
              setJustPosted(null); // le lien d'enrichissement ne traverse pas un changement de canal
              setContactUsedOverride(null); // repart sur le contact par défaut du nouveau canal
              setSuggestion(null);
              setHeroSuggestion(false);
            }}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium ${
              canal === c.type ? "bg-brand-600 text-white" : "text-slate-500 hover:bg-slate-50"
            }`}
          >
            <c.icon size={14} /> {c.label}
          </button>
        ))}
      </div>

      {/* Contact tenté (§3/§5.2) : préaffiché, modifiable — auto-rempli dans le POST */}
      {(contactOptions.length > 0 || instaHandle) && (
        <div className="mb-3 flex items-center gap-2 text-xs">
          <span className="font-medium text-slate-400">Contact tenté :</span>
          {canal === "dm_insta" ? (
            <span className="font-medium text-slate-600">{instaHandle ?? "—"}</span>
          ) : (
            <select
              value={contactUsed ?? ""}
              onChange={(e) => setContactUsedOverride(e.target.value)}
              className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
            >
              {contactOptions.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* Suggestion « mauvais numéro » (§5.2) : visuel seul, AUCUNE mutation */}
      {suggestion && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <span>
            Essayer plutôt : <strong className="font-semibold">{suggestion}</strong>
          </span>
          <button
            onClick={() => {
              setContactUsedOverride(suggestion);
              setSuggestion(null);
            }}
            className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-md bg-white px-2 py-1 text-xs font-medium text-amber-700 ring-1 ring-inset ring-amber-200 hover:bg-amber-100"
          >
            Utiliser ce numéro <ArrowRight size={12} />
          </button>
        </div>
      )}

      {/* Suggestion « RDV pris » (§2 revue produit) : rappelle de marquer le
          statut — visuel + 1 clic explicite, AUCUNE mutation automatique
          (même invariant que la suggestion de numéro ci-dessus). */}
      {heroSuggestion && onMarkRdv && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          <Trophy size={14} className="shrink-0" />
          <span>RDV pris — penser à marquer le statut de la fiche.</span>
          <button
            onClick={() => {
              onMarkRdv();
              setHeroSuggestion(false);
            }}
            className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-md bg-white px-2 py-1 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200 hover:bg-emerald-100"
          >
            Marquer RDV <ArrowRight size={12} />
          </button>
        </div>
      )}

      {/* Émission (email/DM) : action sans résultat encore connu (issue=NULL) */}
      {canal !== "appel" && (
        <button
          onClick={emit}
          disabled={busy === "emit"}
          className="mb-3 flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
        >
          {busy === "emit" ? (
            <Loader2 size={14} className="animate-spin" />
          ) : canal === "email" ? (
            <Mail size={14} />
          ) : (
            <Instagram size={14} />
          )}
          {canal === "email" ? "Email envoyé" : "DM envoyé"}
        </button>
      )}

      {/* Grille de presets N1 x N2 — couleur = issue, 1 tap = 1 POST */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {taxonomy.issues.map((issue) => (
          <div key={issue}>
            <p className={`mb-1.5 text-xs font-semibold uppercase tracking-wide ${issueLabelClass(issue)}`}>
              {QUALIF_ISSUE_LABELS[issue] ?? issue}
              {/* JOINT couvre aussi un refus (« Pas intéressé ») : la personne
                  a bien été jointe, ce qui compte pour la joignabilité —
                  contre-intuitif au premier regard (revue produit), donc
                  explicité ici plutôt qu'en tooltip qu'un débutant ne survole
                  pas. */}
              {issue === "joint" && (
                <span className="ml-1 block text-[10px] font-normal normal-case leading-tight text-slate-400">
                  tu as eu la personne, même si elle refuse
                </span>
              )}
            </p>
            <div className="flex flex-col gap-1.5">
              {(raisonsByIssue[issue] ?? []).map((raison) => {
                const key = `${issue}-${raison}`;
                const isSelected = selected?.issue === issue && selected?.raison === raison;
                const isHero = raison === QUALIF_RAISON_HERO;
                return (
                  <button
                    key={raison}
                    onClick={() => pick(issue, raison)}
                    disabled={busy === key}
                    className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-left text-sm disabled:opacity-60 ${
                      QUALIF_ISSUE_BUTTON_STYLES[issue]
                    } ${isHero ? "border-2 border-emerald-400 font-semibold shadow-sm" : "font-medium"} ${
                      isSelected ? "ring-2 ring-brand-400" : ""
                    }`}
                  >
                    {busy === key ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <>
                        {isHero && <Trophy size={13} className="shrink-0 text-emerald-600" />}
                        {QUALIF_RAISON_LABELS[raison] ?? raison}
                      </>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* + détail (N3 + note), opt-in — jamais requis */}
      <div className="mt-3">
        <button
          onClick={toggleDetail}
          className="text-xs font-medium text-brand-600 hover:text-brand-700"
        >
          {detailOpen ? "− Masquer le détail" : "+ Détail (facultatif)"}
        </button>
        {detailOpen && (
          <div className="mt-2 space-y-2 rounded-lg border border-slate-200 p-3">
            <p className="text-xs text-slate-400">
              {isEnrichingJustPosted
                ? `Déjà enregistrée : ${QUALIF_ISSUE_LABELS[selected!.issue]} · ${
                    QUALIF_RAISON_LABELS[selected!.raison] ?? selected!.raison
                  } — ajoute juste le détail.`
                : selected
                ? `Sélection : ${QUALIF_ISSUE_LABELS[selected.issue]} · ${
                    QUALIF_RAISON_LABELS[selected.raison] ?? selected.raison
                  }`
                : "Choisis une case ci-dessus, puis précise si besoin."}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {taxonomy.details.map((d) => {
                const active = chips.includes(d);
                return (
                  <button
                    key={d}
                    onClick={() =>
                      setChips((cs) => (active ? cs.filter((x) => x !== d) : [...cs, d]))
                    }
                    className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                      active
                        ? "border-brand-300 bg-brand-50 text-brand-700"
                        : "border-slate-200 text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {QUALIF_DETAIL_LABELS[d] ?? d}
                  </button>
                );
              })}
            </div>
            <input
              value={detailNote}
              onChange={(e) => setDetailNote(e.target.value)}
              placeholder="Note libre…"
              className="w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
            <button
              onClick={submitDetailed}
              disabled={!selected || busy === "detail"}
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
            >
              {busy === "detail" ? <Loader2 size={14} className="animate-spin" /> : "Enregistrer"}
            </button>
          </div>
        )}
      </div>

      {/* Note libre indépendante (geste 'note', hors qualification) */}
      <div className="mt-3">
        <button
          onClick={() => setNoteOpen((v) => !v)}
          className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium ${
            noteOpen
              ? "border-brand-300 bg-brand-50 text-brand-700"
              : "border-slate-200 text-slate-700 hover:bg-slate-50"
          }`}
        >
          <Plus size={14} /> Note
        </button>
        {noteOpen && (
          <div className="mt-2 flex gap-2">
            <input
              autoFocus
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitNote()}
              placeholder="Note rapide…"
              className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
            <button
              onClick={submitNote}
              disabled={!noteText.trim() || busy === "note"}
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
            >
              {busy === "note" ? <Loader2 size={14} className="animate-spin" /> : "Ajouter"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// --- Qualification compacte (2 clics) depuis une liste (ex. /followups) ------
// Popover léger : clic sur « Qualifier » (1) puis sur une case (2) = 1 POST.
// Canal fixé sur 'appel' (contexte : liste d'appels). `taxonomy` passée par le
// parent (fetch UNIQUE au niveau page, pas un fetch par ligne).
export function QuickQualifyPopover({
  opportunityId,
  taxonomy,
  phone,
  onAdded,
}: {
  opportunityId: number;
  taxonomy: QualifTaxonomy;
  // Contact tenté (§5.2) : défaut = principal, NON éditable ici (contexte
  // liste, on garde 2 clics — le détail fin reste sur la fiche).
  phone?: string | null;
  onAdded: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  const pick = async (issue: string, raison: string) => {
    const key = `${issue}-${raison}`;
    setBusy(key);
    try {
      await api.addActivity(opportunityId, {
        type: "appel",
        issue,
        raison,
        contact_used: phone ?? undefined,
      });
      onAdded();
      setOpen(false);
    } finally {
      setBusy(null);
    }
  };

  const raisonsByIssue = taxonomy.raisons["appel"] ?? {};

  return (
    <div ref={ref} className="relative z-10">
      <button
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        title="Qualifier l'appel"
        className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium ${
          open
            ? "border-brand-300 bg-brand-50 text-brand-700"
            : "border-slate-200 text-slate-500 hover:bg-slate-50"
        }`}
      >
        <PhoneCall size={12} /> Qualifier
      </button>
      {open && (
        <div
          onClick={(e) => e.stopPropagation()}
          className="absolute right-0 top-full z-20 mt-1 w-60 rounded-lg border border-slate-200 bg-white p-2 shadow-lg"
        >
          {taxonomy.issues.map((issue) => (
            <div key={issue} className="mb-1.5 last:mb-0">
              <p className={`mb-1 text-[10px] font-semibold uppercase tracking-wide ${issueLabelClass(issue)}`}>
                {QUALIF_ISSUE_LABELS[issue] ?? issue}
              </p>
              <div className="flex flex-wrap gap-1">
                {(raisonsByIssue[issue] ?? []).map((raison) => {
                  const key = `${issue}-${raison}`;
                  const isHero = raison === QUALIF_RAISON_HERO;
                  return (
                    <button
                      key={raison}
                      onClick={() => pick(issue, raison)}
                      disabled={busy === key}
                      className={`rounded-md border px-2 py-1 text-[11px] disabled:opacity-60 ${
                        QUALIF_ISSUE_BUTTON_STYLES[issue]
                      } ${isHero ? "border-2 border-emerald-400 font-semibold" : "font-medium"}`}
                    >
                      {busy === key ? (
                        <Loader2 size={11} className="inline animate-spin" />
                      ) : (
                        QUALIF_RAISON_LABELS[raison] ?? raison
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Journal d'activités (plié au-delà de 5) --------------------------------

export function ActivityTimeline({ activities }: { activities: ContactActivity[] }) {
  const [expanded, setExpanded] = useState(false);

  if (activities.length === 0) {
    return <p className="mt-3 text-sm text-slate-400">Aucune activité enregistrée.</p>;
  }

  const shown = expanded ? activities : activities.slice(0, FOLD_AT);
  const hidden = activities.length - shown.length;

  return (
    <div className="mt-3">
      <ul className="divide-y divide-slate-50">
        {shown.map((a) => {
          const Icon = ACTIVITY_ICONS[a.type] ?? StickyNote;
          return (
            <li key={a.id} className="flex items-start gap-2.5 py-2">
              <Icon size={14} className="mt-0.5 shrink-0 text-slate-400" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium text-slate-700">
                      {ACTIVITY_TYPE_LABELS[a.type] ?? a.type}
                    </span>
                    {a.issue && <IssueBadge issue={a.issue} raison={a.raison} />}
                  </div>
                  <span className="shrink-0 text-xs text-slate-400">
                    {formatRelativeDate(a.created_at)}
                  </span>
                </div>
                {/* Contact EFFECTIVEMENT tenté (§5.2 revue produit) : sans lui,
                    plusieurs lignes « Appel · Mauvais numéro » d'affilée sont
                    indiscernables — on ne sait plus quel candidat a été testé. */}
                {a.contact_used && (
                  <p className="mt-0.5 text-xs text-slate-500">{a.contact_used}</p>
                )}
                {a.detail.length > 0 && (
                  <p className="mt-0.5 text-xs text-slate-400">
                    {a.detail.map((d) => QUALIF_DETAIL_LABELS[d] ?? d).join(" · ")}
                  </p>
                )}
                {a.note && (
                  <p className="mt-0.5 text-sm text-slate-500">
                    {a.type === "statut" ? frStatusNote(a.note) : a.note}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {hidden > 0 && (
        <button
          onClick={() => setExpanded(true)}
          className="mt-1 text-xs font-medium text-brand-600 hover:text-brand-700"
        >
          Voir tout ({activities.length})
        </button>
      )}
      {expanded && activities.length > FOLD_AT && (
        <button
          onClick={() => setExpanded(false)}
          className="mt-1 text-xs font-medium text-slate-400 hover:text-slate-600"
        >
          Réduire
        </button>
      )}
    </div>
  );
}

// --- Prochaine action (texte court + date) ----------------------------------

export function NextActionCard({
  opportunityId,
  nextAction,
  nextFollowUpDate,
  onSaved,
}: {
  opportunityId: number;
  nextAction: string | null;
  nextFollowUpDate: string | null;
  onSaved: () => void;
}) {
  const [text, setText] = useState(nextAction ?? "");
  const [date, setDate] = useState(nextFollowUpDate ?? "");
  const [busy, setBusy] = useState(false);

  const hasValue = Boolean(nextAction || nextFollowUpDate);
  const overdue = isOverdue(nextFollowUpDate);

  const save = async () => {
    setBusy(true);
    try {
      await api.setNextAction(opportunityId, {
        next_action: text.trim() || null,
        next_follow_up_date: date || null,
      });
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setBusy(true);
    try {
      await api.setNextAction(opportunityId, {});
      setText("");
      setDate("");
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  // Relance rapide en UN clic : programme une échéance à J+N (date LOCALE, pas
  // toISOString/UTC qui décalerait d'un jour près de minuit) en gardant le texte
  // éventuel. Le geste le plus courant du closer (« ça n'a pas répondu → J+3 »).
  const quickSchedule = async (days: number) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
      d.getDate()
    ).padStart(2, "0")}`;
    setDate(iso);
    setBusy(true);
    try {
      await api.setNextAction(opportunityId, {
        next_action: text.trim() || null,
        next_follow_up_date: iso,
      });
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      {hasValue && (
        <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2.5">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-slate-700">
              {nextAction || "Relance planifiée"}
            </p>
            <p className={`text-xs ${overdue ? "font-medium text-rose-600" : "text-slate-400"}`}>
              {formatDate(nextFollowUpDate)}
              {overdue && " · en retard"}
            </p>
          </div>
          <button
            onClick={clear}
            disabled={busy}
            title="Effacer la prochaine action"
            className="shrink-0 rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50"
          >
            <X size={14} />
          </button>
        </div>
      )}
      <div className="mb-2 flex items-center gap-2">
        <span className="text-xs text-slate-400">Relance rapide :</span>
        <button
          onClick={() => quickSchedule(3)}
          disabled={busy}
          className="rounded-md border border-slate-200 px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          J+3
        </button>
        <button
          onClick={() => quickSchedule(7)}
          disabled={busy}
          className="rounded-md border border-slate-200 px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          J+7
        </button>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ex. rappeler après 14h"
          className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
        />
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
        />
        <button
          onClick={save}
          disabled={busy || (!text.trim() && !date)}
          className="shrink-0 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : "OK"}
        </button>
      </div>
    </div>
  );
}

// --- Assignation (dropdown discret) ------------------------------------------

export function AssignmentSelect({
  opportunityId,
  assignedTo,
  onSaved,
}: {
  opportunityId: number;
  assignedTo: string | null;
  onSaved: () => void;
}) {
  const { user } = useAuth();
  const [users, setUsers] = useState<UserPublic[] | null>(null);
  const [busy, setBusy] = useState(false);

  // Admin SOFT : éditable tant que personne n'est loggé (Alexis aujourd'hui),
  // réservé à l'admin dès qu'une session existe (cohérent avec le 403 backend).
  const editable = !user || user.role === "admin";

  useEffect(() => {
    if (!editable) return;
    api.getUsers().then(setUsers).catch(() => {});
  }, [editable]);

  const change = async (value: string) => {
    setBusy(true);
    try {
      await api.updateAssignment(opportunityId, value || null);
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  if (!editable) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-600">
        <UserCog size={14} className="shrink-0 text-slate-400" />
        {assignedTo ?? "Non assigné"}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <UserCog size={14} className="shrink-0 text-slate-400" />
      <select
        value={assignedTo ?? ""}
        disabled={busy || !users}
        onChange={(e) => change(e.target.value)}
        className="flex-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
      >
        <option value="">Non assigné</option>
        {users?.map((u) => (
          <option key={u.id} value={u.name}>
            {u.name}
          </option>
        ))}
      </select>
    </div>
  );
}
