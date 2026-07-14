import type {
  ActivityJournal,
  ContactActivity,
  DashboardStats,
  FollowUpBuckets,
  FollowUpCount,
  GeneratedMessages,
  GroundtruthResult,
  IngestStats,
  LastIssue,
  Meta,
  OpportunityList,
  OpportunityRead,
  Pipeline,
  QualifStats,
  Settings,
  User,
  UserPublic,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    cache: "no-store",
    credentials: "include", // envoie/reçoit le cookie de session signé
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status} ${path}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export interface OpportunityFilters {
  search?: string;
  city?: string;
  establishment_type?: string;
  main_signal?: string;
  status?: string;
  min_score?: number;
  recommended_channel?: string;
  source?: string;
  lifecycle_label?: string;
  population?: string;
  // Filtre d'assignation : "me" (mes leads) | "none" (non assignés) | <nom>.
  assigned?: string;
  // true = au moins une contact_activity ; false = AUCUNE (« jamais travaillé »,
  // cf. /followups « Jamais appelés ») ; omis = pas de filtre.
  has_activity?: boolean;
  sort_by?: string;
  order?: string;
  limit?: number;
  offset?: number;
}

// Page d'opportunités : lignes + total (en-tête X-Total-Count) pour le pager.
export interface OpportunityPage {
  data: OpportunityList[];
  total: number;
}

// Cache mémoire simple pour /api/meta : la taxonomie de qualification
// (qualif_taxonomy) est statique côté client et lue par chaque instance de
// barre de qualification/puce — évite un fetch par ligne dans les listes.
let metaCache: Meta | null = null;

export const api = {
  getDashboard: () => request<DashboardStats>("/api/dashboard/stats"),

  getMeta: () => request<Meta>("/api/meta"),

  getMetaCached: async (): Promise<Meta> => {
    if (!metaCache) metaCache = await request<Meta>("/api/meta");
    return metaCache;
  },

  getOpportunities: async (
    filters: OpportunityFilters = {}
  ): Promise<OpportunityPage> => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== "" && value !== null) {
        params.append(key, String(value));
      }
    });
    const qs = params.toString();
    const res = await fetch(
      `${API_URL}/api/opportunities${qs ? `?${qs}` : ""}`,
      {
        cache: "no-store",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
      }
    );
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`API ${res.status} /api/opportunities: ${text}`);
    }
    const data = (await res.json()) as OpportunityList[];
    // Total exposé par l'en-tête ; repli sur la taille de page si absent
    // (backend antérieur / en-tête non exposé par un proxy).
    const header = res.headers.get("X-Total-Count");
    const total = header !== null ? Number(header) : data.length;
    return { data, total };
  },

  getOpportunity: (id: number) =>
    request<OpportunityRead>(`/api/opportunities/${id}`),

  updateStatus: (
    id: number,
    body: { status: string; note?: string; next_follow_up_date?: string }
  ) =>
    request<OpportunityRead>(`/api/opportunities/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  generateMessages: (id: number) =>
    request<GeneratedMessages>(`/api/opportunities/${id}/generate-messages`, {
      method: "POST",
    }),

  updateAssignment: (id: number, assignedTo: string | null) =>
    request<OpportunityRead>(`/api/opportunities/${id}/assignment`, {
      method: "PATCH",
      body: JSON.stringify({ assigned_to: assignedTo }),
    }),

  getPipeline: () => request<Pipeline>("/api/pipeline"),

  ingest: (body: {
    source?: string;
    since_days?: number;
    limit?: number;
    departments?: string[];
    reset?: boolean;
  }) =>
    request<IngestStats>("/api/dev/ingest", {
      method: "POST",
      body: JSON.stringify({ source: "bodacc", ...body }),
    }),

  getSettings: () => request<Settings>("/api/settings"),

  updateSettings: (body: Partial<Settings>) =>
    request<Settings>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  getGroundtruth: (asOf?: string) => {
    const qs = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
    return request<GroundtruthResult>(`/api/eval/groundtruth${qs}`);
  },

  // --- Suivi de contact : journal d'activités + prochaine action + relances --

  getActivities: (id: number, limit = 50, offset = 0) =>
    request<ContactActivity[]>(
      `/api/opportunities/${id}/activities?limit=${limit}&offset=${offset}`
    ),

  addActivity: (
    id: number,
    body: {
      type: string;
      note?: string;
      issue?: string;
      raison?: string;
      detail?: string[];
    }
  ) =>
    request<ContactActivity>(`/api/opportunities/${id}/activities`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // Enrichit une qualification déjà postée (N3 : detail/note) SANS créer de
  // doublon — cas « quick tap puis + Détail après coup » (QualificationBar).
  updateActivityDetail: (
    opportunityId: number,
    activityId: number,
    body: { detail?: string[]; note?: string }
  ) =>
    request<ContactActivity>(
      `/api/opportunities/${opportunityId}/activities/${activityId}/detail`,
      { method: "PATCH", body: JSON.stringify(body) }
    ),

  // Puce « dernière issue » (§2.2 du design) : batch, dérivé à la volée,
  // jamais persisté. `ids` = ceux de la page courante (pas de N+1).
  getLastIssues: (ids: number[]): Promise<Record<number, LastIssue>> => {
    if (ids.length === 0) return Promise.resolve({});
    return request<Record<number, LastIssue>>(
      `/api/opportunities/last-issues?ids=${ids.join(",")}`
    );
  },

  setNextAction: (
    id: number,
    body: { next_action?: string | null; next_follow_up_date?: string | null }
  ) =>
    request<OpportunityRead>(`/api/opportunities/${id}/next-action`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  // population omis => défaut backend "architecte" (cohérent avec le reste du
  // produit, pivot Ambient Home) ; passer "" pour toutes les populations.
  // assigned : "me" (mes relances) | "none" | <nom>.
  getFollowUps: (population?: string, assigned?: string) => {
    const params = new URLSearchParams();
    if (population !== undefined) params.set("population", population);
    if (assigned) params.set("assigned", assigned);
    const qs = params.toString();
    return request<FollowUpBuckets>(`/api/followups${qs ? `?${qs}` : ""}`);
  },

  getFollowUpsCount: (population?: string, assigned?: string) => {
    const params = new URLSearchParams();
    if (population !== undefined) params.set("population", population);
    if (assigned) params.set("assigned", assigned);
    const qs = params.toString();
    return request<FollowUpCount>(`/api/followups/count${qs ? `?${qs}` : ""}`);
  },

  // --- Auth légère + vue patron --------------------------------------------

  login: (body: { email: string; password: string }) =>
    request<User>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),

  getMe: () => request<User | null>("/api/auth/me"),

  getUsers: () => request<UserPublic[]>("/api/auth/users"),

  getActivite: (params: { day?: string; author?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.day) qs.set("day", params.day);
    if (params.author) qs.set("author", params.author);
    const s = qs.toString();
    return request<ActivityJournal>(`/api/activite${s ? `?${s}` : ""}`);
  },

  // Onglet « Résultats » de /activite (monitoring, 100% lecture). `period` :
  // preset 'today'|'7j'|'30j' ; `start`/`end` (dates libres) priment s'ils
  // sont fournis (même politique que le backend, cf. _resolve_period).
  getActivityStats: (
    params: { period?: string; start?: string; end?: string } = {}
  ) => {
    const qs = new URLSearchParams();
    if (params.period) qs.set("period", params.period);
    if (params.start) qs.set("start", params.start);
    if (params.end) qs.set("end", params.end);
    const s = qs.toString();
    return request<QualifStats>(`/api/activite/stats${s ? `?${s}` : ""}`);
  },
};
