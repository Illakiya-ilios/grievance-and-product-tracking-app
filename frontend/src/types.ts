export type Role = "admin" | "management" | "region_admin" | "officer";

export interface User {
  id: number;
  name: string;
  email: string;
  role: Role;
  region_id: number | null;
}

export interface Region {
  id: number;
  name: string;
  outlets: { id: number; outlet_code: string; outlet_name: string }[];
}

export interface GrievanceItem {
  id: number;
  reference: string;
  status: string;
  region_id: number;
  region_name: string;
  outlet_id: number | null;
  outlet_name: string | null;
  category: string | null;
  severity: string | null;
  sentiment: string | null;
  summary: string | null;
  confidence: number | null;
  created_at: string;
  due_at: string | null;
  overdue: boolean;
  escalated: boolean;
  assigned_user_id: number | null;
  assigned_user_name: string | null;
  recurring_issue_id: number | null;
  review_reason: string | null;
}

export interface Analysis {
  id: number;
  category: string;
  related_categories: string[];
  sentiment: string;
  severity: string;
  summary: string;
  issues: string[];
  entities: { outlet_id?: string | null; location?: string | null };
  requires_investigation: boolean;
  recommended_action: string;
  confidence: number;
  source: string;
  model: string;
  prompt_version: string;
  error: string | null;
  created_at: string;
}

export interface GrievanceDetail extends GrievanceItem {
  description: string;
  contact_email: string | null;
  reopen_count: number;
  resolved_at: string | null;
  analysis: Analysis | null;
  analysis_history: Analysis[];
  related: {
    grievance_id: number;
    reference: string;
    relationship_type: string;
    direction: string;
    score: number;
    rerank_score: number | null;
    status: string;
    summary: string | null;
    outlet_name: string | null;
    created_at: string;
  }[];
  recurring_issue: { id: number; title: string; category: string; grievance_count: number } | null;
  actions: { action: string; remarks: string; performed_by: string; created_at: string }[];
  permissions: Record<"assign" | "review" | "update_status" | "reopen" | "reanalyze" | "add_note", boolean>;
}

export interface Metrics {
  total: number;
  open: number;
  resolved: number;
  pending_review: number;
  overdue: number;
  escalated: number;
  duplicates: number;
  resolution_rate: number;
  avg_resolution_hours: number | null;
  avg_severity: number | null;
  negative_share: number;
  top_category: string | null;
}

export type Count = { name: string; count: number };
