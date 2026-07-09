import type {
  DashboardStats,
  GeneratedMessages,
  GroundtruthResult,
  IngestStats,
  Meta,
  OpportunityList,
  OpportunityRead,
  Pipeline,
  Settings,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    cache: "no-store",
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
  sort_by?: string;
  order?: string;
}

export const api = {
  getDashboard: () => request<DashboardStats>("/api/dashboard/stats"),

  getMeta: () => request<Meta>("/api/meta"),

  getOpportunities: (filters: OpportunityFilters = {}) => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== "" && value !== null) {
        params.append(key, String(value));
      }
    });
    const qs = params.toString();
    return request<OpportunityList[]>(`/api/opportunities${qs ? `?${qs}` : ""}`);
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
};
