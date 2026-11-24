export const SEVERITY_COLORS: Record<string, string> = {
  Low: "#34d399",
  Medium: "#fbbf24",
  High: "#fb923c",
  Critical: "#ef4444",
};

export const SENTIMENT_COLORS: Record<string, string> = {
  Negative: "#ef4444",
  Neutral: "#94a3b8",
  Positive: "#34d399",
};

const CATEGORY_COLORS: Record<string, string> = {
  "Billing Issues": "#6366f1",
  "Staff Conduct": "#ec4899",
  "Product Availability": "#06b6d4",
  "Outlet Facilities": "#f59e0b",
  "Customer Service": "#8b5cf6",
  "Safety & Security": "#ef4444",
  "Unethical Practices": "#14b8a6",
  Other: "#94a3b8",
};

export const categoryColor = (name: string) => CATEGORY_COLORS[name] ?? "#64748b";

export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "-" : `${Math.round(v * 100)}%`);

export const hours = (h: number | null | undefined) => {
  if (h === null || h === undefined) return "-";
  return h >= 48 ? `${(h / 24).toFixed(1)} d` : `${h.toFixed(1)} h`;
};
