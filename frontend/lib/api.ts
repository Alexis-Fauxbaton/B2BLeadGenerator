import type {
  ActivityJournal,
  ContactActivity,
  DashboardStats,
  FollowUpBuckets,
  FollowUpCount,
  GeneratedMessages,
  GroundtruthResult,
  IngestStats,
  Meta,
  OpportunityList,
  OpportunityRead,
  Pipeline,
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

export const api = {
  getDashboard: () => request<DashboardStats>("/api/dashboard/stats"),

  getMeta: () => request<Meta>("/api/meta"),

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

  addActivity: (id: number, body: { type: string; note?: string }) =>
    request<ContactActivity>(`/api/opportunities/${id}/activities`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

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
};
