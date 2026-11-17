"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Bell, Bookmark, Check, ClipboardList, Mail, ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api, dateLabel, errorText, type Institute, type Opportunity, type Profile } from "./client";
import { EventCard, Heading, Notice } from "./site";

export function Account({ profile, institutes, onRefresh, onSave }: {
  profile: Profile | null; institutes: Institute[]; onRefresh: () => Promise<void>; onSave: (event: Opportunity) => void;
}) {
  const router = useRouter();
  const [signup, setSignup] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const location = new URL(window.location.href);
    const token = location.searchParams.get("verify");
    if (!token) return;
    location.searchParams.delete("verify");
    window.history.replaceState(window.history.state, "", location);
    api("/account/verify", { method: "POST", body: JSON.stringify({ token }) })
      .then(() => setMessage("Email verified. You can sign in now.")).catch((error) => setError(errorText(error)));
  }, []);

  async function action(task: () => Promise<unknown>, success?: string) {
    setBusy(true); setError(""); setMessage("");
    try { await task(); await onRefresh(); if (success) setMessage(success); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await action(async () => {
      const response = await api<{ verification_required?: boolean }>("/account/" + (signup ? "signup" : "login"), {
        method: "POST", body: JSON.stringify({ email: form.get("email"), password: form.get("password"),
          ...(signup ? { adult: form.has("adult"), consent: form.has("consent") } : {}) }),
      });
      if (response.verification_required) {
        setSignup(false);
        setMessage("Check your email for a verification link.");
      } else {
        const from = new URLSearchParams(window.location.search).get("from");
        if (from && /^feedback(?:\/\d+)?$/.test(from)) router.replace("/" + from);
      }
    });
  }

  if (!profile) return <section className="wrap inner-page auth-page">
    <Heading label="MY AJORA" title={signup ? "Create your account." : "Welcome back."} text="Save opportunities and get alerts for events you follow." />
    <form onSubmit={authenticate} className="form-panel">
      {message && <Notice>{message}</Notice>}{error && <Notice error>{error}</Notice>}
      <label>Email address<input name="email" type="email" maxLength={254} autoComplete="email" required /></label>
      <label>Password<input name="password" type="password" minLength={12} maxLength={128} autoComplete={signup ? "new-password" : "current-password"} required /></label>
      {signup && <><small>Use at least 12 characters. Pilot accounts are available to students aged 18 and over.</small>
        <label className="checkbox"><input name="adult" type="checkbox" required /> I am 18 or older.</label>
        <label className="checkbox"><input name="consent" type="checkbox" required /> I agree to AJORA storing my email, password hash, bookmarks and alert preferences to provide my account.</label>
      </>}
      <button className="button purple" disabled={busy} type="submit">{signup ? "Create account" : "Sign in"} <ArrowRight size={16} /></button>
      <button type="button" className="text-button" onClick={() => { setSignup(!signup); setMessage(""); setError(""); }}>{signup ? "Already have an account? Sign in" : "New to AJORA? Create an account"}</button>
    </form></section>;

  return <section className="wrap inner-page">
    <Heading label="MY AJORA" title="Your opportunities." text={profile.email}><button className="button outline" disabled={busy} onClick={() => action(() => api("/account/logout", { method: "POST" }))}>Sign out</button></Heading>
    {message && <Notice>{message}</Notice>}{error && <Notice error>{error}</Notice>}
    <div className="account-grid">
      <form className="form-panel" key={JSON.stringify(profile.preferences)} onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        void action(() => api("/account/preferences", { method: "PUT", body: JSON.stringify({ alerts: form.has("alerts"), institutes: form.getAll("institutes") }) }), "Alert preferences saved");
      }}>
        <h2><Bell size={21} /> Alert preferences</h2>
        <label className="checkbox"><input type="checkbox" name="alerts" defaultChecked={profile.preferences.alerts} /> Enable in-app event and deadline alerts</label>
        <label>Follow institutes<select name="institutes" multiple size={5} defaultValue={profile.preferences.institutes}>{institutes.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <small>Use Ctrl or Command to select multiple institutes.</small>
        <button className="button purple" disabled={busy}>Save preferences</button>
      </form>
      <div className="form-panel"><h2><Mail size={21} /> Notifications</h2>
        {!profile.notifications.length && <p>No alerts yet.</p>}
        {profile.notifications.map((item) => <div className="alert-row" key={item.id}><div><Link href={"/events/" + item.event_id}>{item.message}</Link><small>{dateLabel(item.created_at)}</small></div>
          {!item.read && <button className="icon-button" disabled={busy} aria-label="Mark as read" onClick={() => action(() => api("/account/notifications/" + item.id, { method: "PUT" }))}><Check size={17} /></button>}</div>)}
      </div>
    </div>
    <h2 className="mini-heading"><Bookmark size={23} /> Saved events</h2>
    {profile.saved.length ? <div className="card-grid">{profile.saved.map((event) => <EventCard key={event.id} event={event} saved onSave={onSave} />)}</div> :
      <div className="empty"><Bookmark size={26} /><h3>No saved events</h3><Link href="/explore">Find an opportunity</Link></div>}
    <details className="privacy"><summary>Account data</summary><p>Delete your account, bookmarks, alerts and feedback posts.</p><button className="button danger" disabled={busy}
      onClick={() => { if (window.confirm("Permanently delete your AJORA account?")) void action(() => api("/account", { method: "DELETE" }), "Account deleted."); }}>Delete account</button></details>
  </section>;
}

export function Organizer({ profile, institutes }: { profile: Profile | null; institutes: Institute[] }) {
  const [event, setEvent] = useState<Opportunity | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("event");
    if (id) api<Opportunity>("/events/" + encodeURIComponent(id)).then(setEvent).catch((error) => setError(errorText(error)));
  }, []);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const target = e.currentTarget;
    const form = new FormData(target);
    setError(""); setMessage(""); setBusy(true);
    try {
      await api("/submissions", { method: "POST", body: JSON.stringify({ institute_id: form.get("institute"), source_url: form.get("url"),
        notes: form.get("notes"), event_id: event?.id ?? null }) });
      setMessage("Sent to the AJORA review queue."); target.reset();
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  return <section className="wrap inner-page auth-page">
    <Heading label="ORGANIZER PORTAL" title={event ? "Report a correction." : "Submit an announcement."}
      text={event ? event.title : "Share an official institute event page for AJORA to review."} />
    {error && <Notice error>{error}</Notice>}{message && <Notice>{message}</Notice>}
    {!profile ? <Notice><Link href="/my-ajora">Sign in</Link> to submit an announcement or correction.</Notice> :
      <form className="form-panel" onSubmit={submit}>
        <label>Institute<select name="institute" key={event?.institute.id ?? "institute"} defaultValue={event?.institute.id ?? ""} required>
          <option value="">Choose an institute</option>{institutes.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
        </select></label>
        <label>Official announcement URL<input name="url" type="url" key={event?.source_url ?? "url"} defaultValue={event?.source_url ?? ""} placeholder="https://" required maxLength={1000} /></label>
        <label>Event details or correction<textarea name="notes" required minLength={12} maxLength={2000} rows={5} /></label>
        <button className="button purple" disabled={busy}>Submit for review <ArrowRight size={16} /></button>
      </form>}
  </section>;
}

type Review = { id: number; kind: string; snapshot_id: number | null; event_id: string | null;
  proposal: { candidate?: unknown; evidence?: unknown; possible_matches?: { id: string; label: string }[];
    priority?: number; critical_checks?: string[]; extraction_agent?: string | null;
    schedule_notice?: { provider: string; excerpt: string; change: string } | null;
    [key: string]: unknown }; context: string; created_at: string };
type Monitor = { sources: { id: number; url: string; approved: boolean; failures: number; last_success: string | null }[];
  runs: { source_id: number; outcome: string; details: unknown; created_at: string }[]; configured_agents: string[] };
type ApplicationLink = { url: string; method: string; location: string; label: string };
type MaterialChoice = { key: string; label: string; kind: "pdf" | "image"; url: string | null };
type Snapshot = { url: string; captured_at: string; digest: string; text: string; application_links: ApplicationLink[]; materials: MaterialChoice[] };

export function ReviewDesk() {
  const [token, setToken] = useState("");
  const [active, setActive] = useState("");
  const [reviews, setReviews] = useState<Review[]>([]);
  const [monitor, setMonitor] = useState<Monitor | null>(null);
  const [selected, setSelected] = useState<Review | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [registration, setRegistration] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [pricesConfirmed, setPricesConfirmed] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("queue");

  const request = <T,>(path: string, key: string, options?: RequestInit) =>
    api<T>("/admin" + path, { ...options, headers: { "X-Ajora-Admin": key } });

  async function refresh(key: string) {
    const [queue, monitor] = await Promise.all([
      request<Review[]>("/reviews", key), request<Monitor>("/monitor", key),
    ]);
    setReviews(queue); setMonitor(monitor); setActive(key); setToken("");
  }

  function choose(item: Review) {
    setSelected(item); setSnapshot(null); setError(""); setMessage("");
    setRegistration((item.proposal.candidate as { registration_url?: string } | undefined)?.registration_url ?? "");
    setConfirmed(false);
    setPricesConfirmed(false);
  }

  useEffect(() => {
    if (!selected?.snapshot_id) return;
    const controller = new AbortController();
    request<Snapshot>("/snapshots/" + selected.snapshot_id, active, { signal: controller.signal }).then(setSnapshot)
      .catch((error) => { if (!controller.signal.aborted) setError(errorText(error)); });
    return () => controller.abort();
  }, [selected, active]);

  async function resolve(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = new FormData(event.currentTarget);
    const action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value");
    setBusy(true); setError(""); setMessage("");
    try {
      const candidate = action === "publish" ? { ...JSON.parse(String(form.get("candidate"))), registration_url: registration.trim() || null } : undefined;
      const evidence = action === "publish" ? JSON.parse(String(form.get("evidence"))) : undefined;
      await request("/reviews/" + selected.id, active, { method: "POST",
        body: JSON.stringify({ action, note: form.get("note"), candidate, evidence,
          target_id: form.get("target_id") || (["attach_application", "attach_materials"].includes(action ?? "") ? selected.event_id : null),
          material_keys: form.getAll("material_keys"), separate_session: form.has("separate_session"),
          application_confirmed: confirmed, prices_confirmed: pricesConfirmed,
          registration_url: action === "attach_application" ? registration.trim() : undefined }) });
      setSelected(null); setSnapshot(null); await refresh(active); setMessage("Review decision saved.");
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  return <section className="wrap inner-page"><Heading label="OPERATIONS" title="Review desk." text="Review source permissions, event claims and monitoring results." />
    {error && <Notice error>{error}</Notice>}{message && <Notice>{message}</Notice>}
    {!active ? <form className="form-panel narrow" onSubmit={async (event) => {
      event.preventDefault(); setError(""); setBusy(true);
      try { await refresh(token); } catch (error) { setError(errorText(error)); } finally { setBusy(false); }
    }}><label>Administrator key<input type="password" autoComplete="off" required value={token} onChange={(event) => setToken(event.target.value)} /></label>
      <button className="button purple" disabled={busy}><ShieldCheck size={18} /> Open review desk</button></form> :
      <><div className="tabs"><button className={tab === "queue" ? "active" : ""} onClick={() => setTab("queue")}>Review queue ({reviews.length})</button><button className={tab === "monitor" ? "active" : ""} onClick={() => setTab("monitor")}>Monitoring</button>
        <button onClick={() => refresh(active).catch((error) => setError(errorText(error)))}>Refresh</button>
        <button onClick={() => { setActive(""); setSelected(null); setSnapshot(null); setReviews([]); setMonitor(null); setError(""); }}>Lock</button></div>
        {tab === "monitor" && monitor && <div className="account-grid"><div className="form-panel"><h2>Registered sources</h2>{monitor.sources.map((source) => <div key={source.id} className="alert-row"><a href={source.url} target="_blank" rel="noreferrer">{source.url}</a><small>{source.approved ? source.failures ? source.failures + " failed checks" : source.last_success ? dateLabel(source.last_success) : "Awaiting first check" : "Permission pending"}</small></div>)}</div>
          <div className="form-panel"><h2>Recent runs</h2><p>{monitor.configured_agents?.length ? "Configured in this API process · " + monitor.configured_agents.join(", ") : "Python checks active. AI provider setup pending in this API process."}</p>{!monitor.runs.length && <p>No crawls have run.</p>}{monitor.runs.map((run, index) => <details key={index}><summary>{run.outcome} · Source {run.source_id} · {dateLabel(run.created_at)}</summary><pre>{JSON.stringify(run.details, null, 2)}</pre></details>)}</div></div>}
        {tab === "queue" && (!reviews.length ? <div className="empty"><ClipboardList size={32} /><h3>No pending reviews</h3></div> :
          <div className="review-grid"><div className="form-panel">{reviews.map((item) => <button key={item.id} className={"review-item " + (selected?.id === item.id ? "active" : "")} onClick={() => choose(item)}>
            <strong className="capitalize">{item.kind}</strong> #{item.id}{!!item.proposal.priority && " · Priority"}<small>{String((item.proposal.candidate as { title?: string } | undefined)?.title ?? item.proposal.url ?? item.proposal.source_url ?? item.context)}</small>
          </button>)}</div>
            {selected && <form key={selected.id} className="form-panel" onSubmit={resolve}>
              <h2 className="capitalize">{selected.kind} #{selected.id}</h2><p>{selected.context}</p>
              {!!selected.proposal.critical_checks?.length && <ul>{selected.proposal.critical_checks.map((check) => <li key={check}>{check}</li>)}</ul>}
              {selected.proposal.schedule_notice && <div><strong>{selected.proposal.schedule_notice.provider} flagged {selected.proposal.schedule_notice.change}</strong>
                <blockquote>{selected.proposal.schedule_notice.excerpt}</blockquote></div>}
              {selected.proposal.extraction_agent && <p>Extraction assisted by {selected.proposal.extraction_agent}</p>}
              {snapshot && <details open><summary>Collected snapshot · {dateLabel(snapshot.captured_at)}</summary><a href={snapshot.url} target="_blank" rel="noreferrer">{snapshot.url}</a><pre className="snapshot">{snapshot.text}</pre></details>}
              {["candidate", "change", "document"].includes(selected.kind) ? <>
                <label>Candidate JSON<textarea name="candidate" rows={10} onChange={() => setPricesConfirmed(false)} defaultValue={JSON.stringify(selected.proposal.candidate ?? { title: "", starts: "", category: "Other" }, (key, value) => key === "registration_url" ? undefined : value, 2)} /></label>
                <label>Field evidence JSON<textarea name="evidence" rows={7} onChange={() => setPricesConfirmed(false)} defaultValue={JSON.stringify(selected.proposal.evidence ?? { title: "", starts: "" }, null, 2)} /></label>
                <label className="checkbox"><input type="checkbox" checked={pricesConfirmed} onChange={(event) => setPricesConfirmed(event.target.checked)} /> I double-checked the listed prices, any deposits, and certificate costs for each attendance option and eligible group.</label>
                <label>Application URL<input type="url" value={registration} onChange={(event) => { setRegistration(event.target.value); setConfirmed(false); }} maxLength={1000} /></label>
                {!!snapshot?.application_links.length && <div className="review-applications">
                  <h3>Links found in the announcement</h3>
                  {snapshot.application_links.map((link, index) => <div key={index} className="review-application">
                    <div><a href={link.url} target="_blank" rel="noreferrer">{link.url}</a><small>{link.method} · {link.location}{link.label && " · " + link.label}</small></div>
                    <button className="button outline small" type="button" onClick={() => { setRegistration(link.url); setConfirmed(false); }}>Use link</button>
                  </div>)}
                </div>}
                {registration && <label className="checkbox"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> I checked that this link opens the registration page for this event.</label>}
                {!!snapshot?.materials.length && <fieldset className="review-materials"><legend>Flyers and posters for this event</legend>
                  {snapshot.materials.map((material) => <div key={material.key}>
                    <label className="checkbox"><input type="checkbox" name="material_keys" value={material.key} defaultChecked={material.key === "original"} disabled={material.key === "original"} /> {material.label}</label>
                    {material.url && <a href={material.url} target="_blank" rel="noreferrer">Open institute file</a>}
                  </div>)}
                </fieldset>}
                {!selected.event_id && <label>Existing event ID for an update<input name="target_id" placeholder="The ID shown in the event page URL" maxLength={80} list="matches" /><datalist id="matches">{selected.proposal.possible_matches?.map((match) => <option value={match.id} label={match.label} key={match.id} />)}</datalist></label>}
                {!!selected.proposal.possible_matches?.length && <label className="checkbox"><input type="checkbox" name="separate_session" /> This is a distinct event or session.</label>}
              </> : <pre>{JSON.stringify(selected.proposal, null, 2)}</pre>}
              <label>Decision note<textarea name="note" rows={3} required minLength={8} maxLength={1000} /></label>
              <div className="panel-actions">
                {selected.event_id && ["cancelled", "postponed"].includes(selected.proposal.schedule_notice?.change ?? "") &&
                  <button className="button outline" value="confirm_schedule_notice" disabled={busy}>Confirm {selected.proposal.schedule_notice?.change === "cancelled" ? "cancellation" : "postponement"}</button>}
                {["candidate", "change", "document"].includes(selected.kind) ? <button className="button purple" value="publish" disabled={busy}>Approve and publish</button> : <button className="button purple" value="approve_source" disabled={busy}>Approve source permission</button>}
                {registration && ["candidate", "change", "document"].includes(selected.kind) && <button className="button outline" value="attach_application" disabled={busy || !confirmed}>Update application link only</button>}
                {!!snapshot?.materials.length && ["candidate", "change", "document"].includes(selected.kind) && <button className="button outline" value="attach_materials" disabled={busy}>Attach flyers and posters only</button>}
                <button className="button outline" value="reject" disabled={busy}>Reject</button></div>
            </form>}</div>)}
      </>}
  </section>;
}
