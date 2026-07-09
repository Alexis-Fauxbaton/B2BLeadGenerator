export interface OpportunityList {
  id: number;
  establishment_name: string;
  establishment_type: string;
  city: string;
  address: string;
  main_signal: string;
  secondary_signals: string[];
  detection_date: string;
  activity_start_date: string | null;
  venue_origin_date: string | null;
  estimated_timing: string;
  probable_needs: string[];
  decision_maker: string | null;
  dirigeants: string[];
  opportunity_score: number;
  score_reason: string;
  recommended_channel: string;
  channel_reason: string;
  proof_text: string;
  proof_url: string;
  status: string;
  source: string;
  source_ref: string | null;
  siren: string | null;
  naf: string | null;
  phone: string | null;
  email: string | null;
  website: string | null;
  instagram: string | null;
  facebook: string | null;
  latitude: number | null;
  longitude: number | null;
  review_count: number | null;
  lifecycle_stage: string;
  heat: string;
  freshness: string;
  contact_confidence: string | null;
  decision_maker_email: string | null;
  decision_maker_confidence: string | null;
  next_follow_up_date: string | null;
  created_at: string;
  updated_at: string;
}

export interface IngestStats {
  source: string;
  fetched: number;
  chr_matched: number;
  created: number;
  updated: number;
  skipped_dupes: number;
  errors: number;
}

export interface SignalRead {
  id: number;
  signal_type: string;
  source: string;
  source_url: string;
  signal_date: string;
  confidence_score: number;
  raw_text: string;
}

export interface ContactHistoryRead {
  id: number;
  channel: string | null;
  message: string | null;
  action_type: string;
  status: string | null;
  note: string | null;
  contacted_at: string | null;
  created_at: string;
}

export interface OpportunityRead extends OpportunityList {
  generated_instagram_dm: string | null;
  generated_email: string | null;
  generated_linkedin: string | null;
  generated_call_script: string | null;
  signals: SignalRead[];
  contact_history: ContactHistoryRead[];
}

export interface GeneratedMessages {
  instagram_dm: string;
  email: string;
  linkedin: string;
  call_script: string;
  source: string;
}

export interface DashboardStats {
  total_opportunities: number;
  hot_leads: number;
  not_contacted: number;
  follow_ups_due: number;
  interested: number;
  appointments: number;
  won: number;
  lost: number;
  by_signal: { label: string; count: number }[];
  by_status: { label: string; count: number }[];
  hottest: OpportunityList[];
}

export interface Settings {
  id: number;
  provider_name: string;
  provider_offer: string;
  tone: string;
  target_area: string;
  updated_at: string;
}

export interface Meta {
  establishment_types: string[];
  signal_types: string[];
  channels: string[];
  statuses: string[];
  cities: string[];
}

export type Pipeline = Record<string, OpportunityList[]>;

export interface GroundtruthRow {
  handle: string;
  name: string;
  label: string;
  confidence: string;
  rationale: string;
  annotated_at: string;
  ig_url: string;
  has_snapshot: boolean;
  predicted: string | null;
  disagreement: boolean;
}

export interface GroundtruthResult {
  as_of: string | null;
  total: number;
  rows: GroundtruthRow[];
}
