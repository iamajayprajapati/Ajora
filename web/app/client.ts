export interface Institute {
  id: string;
  name: string;
  category: string;
  city: string;
  state: string;
  domains: string[];
  registry_url: string;
  official_url?: string;
  subgroup?: string;
  logo_url?: string;
  logo_source?: string;
  introduction?: string;
  about_url?: string;
  website_status?: "reachable" | "official_confirmed" | "unavailable";
  event_monitor?: { status: "pending" | "unavailable" | "partial" | "checked"; enabled: number; pending: number;
    failed: number; last_success: string | null; last_checked: string | null };
  website_checked_on?: string;
  upcoming_events?: number;
  recent_events?: number;
  sample_events?: number;
}

export interface Attendance {
  mode: string;
  levels: string[];
  fee_inr: number | null;
  deposit_inr: number | null;
  certificate: string;
  certificate_fee_inr: number | null;
  certificate_conditions: string | null;
  conditions: string | null;
}

export interface Opportunity {
  id: string;
  title: string;
  institute: Institute;
  category: string;
  description: string | null;
  subject: string[];
  starts: string;
  ends: string | null;
  registration_deadline: string | null;
  registration_url: string | null;
  registration_external?: boolean;
  checked_at: string | null;
  source_url?: string;
  status: string;
  venue: string | null;
  eligibility_notes: string | null;
  difficulty: string | null;
  language: string | null;
  assistance: string | null;
  learning_outcomes: string | null;
  attendance: Attendance[];
  entities: { name: string; role: string; details: string | null }[];
  verification: "sample" | "verified" | "stale";
  demo: boolean;
  new?: boolean;
  price_review_pending?: boolean;
  date_review_pending?: boolean;
  evidence?: { field: string; excerpt: string }[];
  materials?: EventMaterial[];
}

export interface EventMaterial {
  label: string;
  kind: "pdf" | "image";
  official_url: string | null;
  preview_url: string | null;
}

export interface Profile {
  email: string;
  preferences: { alerts: boolean; institutes: string[] };
  saved: Opportunity[];
  notifications: { id: number; event_id: string; message: string; created_at: string; read: boolean }[];
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch("/api" + path, {
    cache: "no-store",
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = data?.detail;
    throw new ApiError(response.status, typeof detail === "string" ? detail :
      Array.isArray(detail) ? detail.map((error) => error.msg).join(". ") :
      "AJORA could not complete the request. Check that the API is running and try again.");
  }
  return response.json() as Promise<T>;
}

export const errorText = (error: unknown) => error instanceof Error ? error.message : "Something went wrong. Try again.";
export const dateLabel = (date: string) =>
  new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" }).format(new Date(date));
export const rupees = (fee: number) => "₹" + fee.toLocaleString("en-IN");
export const freeEntry = (option: Attendance) => option.fee_inr === 0 && !option.deposit_inr;
export const freeTogether = (option: Attendance) =>
  freeEntry(option) && option.certificate === "provided" && option.certificate_fee_inr === 0;

export function upfrontFee(option: Attendance) {
  return option.fee_inr === null ? null : option.fee_inr + (option.deposit_inr ?? 0);
}

export const indianToday = () => new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());

export function applicationOpen(event: Opportunity) {
  const today = indianToday();
  return !event.date_review_pending && event.status === "scheduled" && (event.ends ?? event.starts) >= today &&
    (!event.registration_deadline || event.registration_deadline >= today);
}
