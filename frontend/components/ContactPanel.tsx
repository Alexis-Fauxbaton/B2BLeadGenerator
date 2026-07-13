"use client";

// Suivi de contact SOBRE (critère d'acceptation « pas le fouilli ») : boutons
// rapides + journal d'activités compact + une prochaine action par fiche.
// Regroupé ici pour rester réutilisable et garder page.tsx lisible.

import { useEffect, useState } from "react";
import {
  Phone,
  PhoneCall,
  PhoneMissed,
  Voicemail,
  Mail,
  Instagram,
  StickyNote,
  ArrowRightLeft,
  Loader2,
  X,
  Plus,
  UserCog,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ContactActivity, UserPublic } from "@/lib/types";
import { useAuth } from "@/lib/auth";
import {
  ACTIVITY_TYPE_LABELS,
  STATUS_LABELS,
  formatDate,
  formatRelativeDate,
  isOverdue,
} from "@/lib/labels";

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

// --- Boutons rapides ---------------------------------------------------------

export function QuickActions({
  opportunityId,
  onAdded,
}: {
  opportunityId: number;
  onAdded: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteText, setNoteText] = useState("");

  const fire = async (type: string, note?: string, key?: string) => {
    setBusy(key ?? type);
    try {
      await api.addActivity(opportunityId, note ? { type, note } : { type });
      onAdded();
    } finally {
      setBusy(null);
    }
  };

  const submitNote = async () => {
    const note = noteText.trim();
    if (!note) return;
    await fire("note", note);
    setNoteText("");
    setNoteOpen(false);
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        {/* Issue d'appel en 1 clic : type reste 'appel', seule la note change. */}
        <QuickButton
          icon={PhoneCall}
          label="Répondu"
          busy={busy === "appel-repondu"}
          onClick={() => fire("appel", "Répondu", "appel-repondu")}
        />
        <QuickButton
          icon={PhoneMissed}
          label="Pas de réponse"
          busy={busy === "appel-absent"}
          onClick={() => fire("appel", "Pas de réponse", "appel-absent")}
        />
        <QuickButton
          icon={Voicemail}
          label="Répondeur"
          busy={busy === "appel-repondeur"}
          onClick={() => fire("appel", "Répondeur", "appel-repondeur")}
        />
        <QuickButton
          icon={Mail}
          label="Email envoyé"
          busy={busy === "email"}
          onClick={() => fire("email")}
        />
        <QuickButton
          icon={Instagram}
          label="DM envoyé"
          busy={busy === "dm_insta"}
          onClick={() => fire("dm_insta")}
        />
        <QuickButton
          icon={Plus}
          label="Note"
          busy={false}
          onClick={() => setNoteOpen((v) => !v)}
          active={noteOpen}
        />
      </div>

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
  );
}

function QuickButton({
  icon: Icon,
  label,
  busy,
  active,
  onClick,
}: {
  icon: typeof Phone;
  label: string;
  busy: boolean;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium disabled:opacity-60 ${
        active
          ? "border-brand-300 bg-brand-50 text-brand-700"
          : "border-slate-200 text-slate-700 hover:bg-slate-50"
      }`}
    >
      {busy ? <Loader2 size={14} className="animate-spin" /> : <Icon size={14} />}
      {label}
    </button>
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
                  <span className="text-sm font-medium text-slate-700">
                    {ACTIVITY_TYPE_LABELS[a.type] ?? a.type}
                  </span>
                  <span className="shrink-0 text-xs text-slate-400">
                    {formatRelativeDate(a.created_at)}
                  </span>
                </div>
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
