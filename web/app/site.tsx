"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, ArrowUpRight, Award, Bookmark, Building2, CalendarDays, CircleHelp, Code2, Compass, FileText, FlaskConical, GraduationCap, Image as ImageIcon, Landmark, Laptop, ListFilter, MapPin, Search, ShieldCheck, Sparkles, Users, X, Zap } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, api, applicationOpen, dateLabel, errorText, freeEntry, freeTogether, indianToday, rupees, upfrontFee, type Attendance, type Institute, type Opportunity, type Profile } from "./client";
import { Account, Organizer, ReviewDesk } from "./panels";
import { Feedback } from "./feedback";

const sections = [
  { key: "IIT", slug: "iit", title: "IITs", name: "Indian Institutes of Technology", Icon: GraduationCap },
  { key: "NIT", slug: "nit", title: "NITs", name: "National Institutes of Technology", Icon: Building2 },
  { key: "IIIT", slug: "iiit", title: "IIITs", name: "Information Technology Institutes", Icon: Code2 },
  { key: "Central University", slug: "central-university", title: "Central Universities", name: "UGC-listed universities", Icon: Landmark },
];
const categories = ["Workshop", "Conference", "Hackathon", "Seminar", "Training", "Summer school", "FDP", "Other"];
const levels: [string, string][] = [["Class 9–12", "School students"], ["UG", "Undergraduates"], ["PG", "Postgraduates"], ["PhD", "Doctoral researchers"], ["Faculty", "Faculty & educators"]];
const levelLabels = new Map(levels);
const instituteTheme = (key: string) => sections.find((section) => section.key === key)?.slug;
const iiitSubgroups = ["Centrally funded", "PPP institutes", "Additional campus", "Other IIITs"];
let visitorRequest: Promise<{ total: number }> | undefined;

function Brand() {
  return <Link href="/" className="brand" aria-label="AJORA home"><span className="brand-mark"><Sparkles size={21} strokeWidth={2.7} /></span> AJORA<span className="brand-dot">.</span></Link>;
}

export function Heading({ label, title, text, children }: { label?: string; title: string; text?: string; children?: ReactNode }) {
  return <div className="section-head">
    <div>{label && <div className="eyebrow"><span />{label}</div>}<h1>{title}</h1>{text && <p>{text}</p>}</div>{children}
  </div>;
}

export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={error ? "notice error" : "notice"} role={error ? "alert" : "status"}>
    <CircleHelp size={18} /> <div>{children}</div>
  </div>;
}

function Header({ route, profile }: { route: string; profile: Profile | null }) {
  const nav = [["/", "Home"], ["/explore", "Explore"], ["/freelearn", "FreeLearn"], ["/institutes", "Institutes"], ["/feedback", "Feedback"], ["/about", "About Us"]];
  return <header className="header"><div className="wrap header-inner">
    <Brand />
    <nav aria-label="Main navigation">{nav.map(([url, label]) => <Link key={url} href={url} aria-current={(route ? "/" + route : "/") === url ? "page" : undefined}>{label}</Link>)}</nav>
    <Link href="/my-ajora" className="button dark small">{profile ? "My AJORA" : "Sign in"} <ArrowUpRight size={15} /></Link>
  </div></header>;
}

function Footer() {
  const [visitors, setVisitors] = useState<number | null>(null);
  useEffect(() => {
    let mounted = true;
    visitorRequest ??= api<{ total: number }>("/visits", { method: "POST" }).catch((error) => {
      visitorRequest = undefined;
      throw error;
    });
    visitorRequest.then((result) => { if (mounted) setVisitors(result.total); }).catch(() => {});
    return () => { mounted = false; };
  }, []);
  return <footer className="footer"><div className="wrap footer-inner">
    <div><Brand /><p>For every student, everywhere in India.</p></div>
    <nav aria-label="Footer navigation"><Link href="/explore">Explore events</Link><Link href="/institutes">Institute directory</Link><Link href="/about">About Us</Link><Link href="/feedback">Feedback forum</Link><Link href="/organizer">Organizer portal</Link><Link href="/review">Review desk</Link></nav>
    {visitors !== null && <div className="visitor-count" title="Counted once per browser while the visitor cookie is present"><Users size={18} /><span><strong>{visitors.toLocaleString("en-IN")}</strong> {visitors === 1 ? "visitor" : "visitors"}</span></div>}
  </div></footer>;
}

function Hero({ onSearch }: { onSearch: (query: string) => void }) {
  const [query, setQuery] = useState("");
  return <section className="hero">
    <div className="wrap hero-grid">
      <div className="hero-copy">
        <div className="eyebrow"><span /> ACADEMIC EVENTS ACROSS INDIA</div>
        <h1>Find your next<br /> <em>academic event.</em></h1>
        <p>Discover workshops, hackathons and research events. Compare fees, certificates and ways to attend before you apply.</p>
        <form className="hero-search" onSubmit={(event) => { event.preventDefault(); onSearch(query); }}>
          <Search size={20} /><input aria-label="Search events" placeholder="Search by topic, event or institute" value={query} onChange={(event) => setQuery(event.target.value)} />
          <button className="button purple" type="submit">Search <ArrowRight size={16} /></button>
        </form>
        <div className="popular"><span>Popular</span>{["AI", "Hackathon", "Research"].map((topic) =>
          <button key={topic} onClick={() => onSearch(topic)}>{topic}</button>)}</div>
      </div>
      <div className="hero-art">
        {/* Local art uses native sizing to retain the print-style crop. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/campus.webp" alt="Three students collaborating around a laptop on campus" fetchPriority="high" />
        <div className="art-note"><span><ShieldCheck size={20} /></span><div>Official sources<small>Evidence linked to each listing</small></div></div>
      </div>
    </div>
  </section>;
}

const theme = (event: Opportunity) => {
  if (event.category === "Hackathon") return ["peach", Zap] as const;
  if (event.category === "Training") return ["mint", Code2] as const;
  if (event.category === "Seminar") return ["sand", FlaskConical] as const;
  if (event.category === "Conference") return ["blue", Landmark] as const;
  return ["lavender", Laptop] as const;
};

function Badge({ children, color = "" }: { children: ReactNode; color?: string }) {
  return <span className={"badge " + color}>{children}</span>;
}

function PriceSummary({ event }: { event: Opportunity }) {
  if (event.price_review_pending) return <Badge color="price-unknown">Price check pending</Badge>;
  const hasFree = event.attendance.some(freeEntry);
  const allFree = event.attendance.every(freeEntry);
  const paid = event.attendance.filter((option) => (upfrontFee(option) ?? 0) > 0);
  const minimum = paid.length ? Math.min(...paid.map((option) => upfrontFee(option)!)) : null;
  const hasUnknown = !event.attendance.length || event.attendance.some((option) => option.fee_inr === null);
  return <>
    {hasFree && <Badge color="price-free">{allFree ? "Free entry" : "Free entry available"}</Badge>}
    {minimum !== null && <Badge color="price-paid">{paid.some((option) => upfrontFee(option) !== minimum) ? "From " : ""}{rupees(minimum)}{paid.some((option) => upfrontFee(option) === minimum && option.deposit_inr) ? " upfront" : " entry"}</Badge>}
    {hasUnknown && <Badge color="price-unknown">{hasFree || minimum !== null ? "Some fees unannounced" : "Fee unannounced"}</Badge>}
    {event.attendance.some(freeTogether) && <Badge color="price-free"><Award size={13} />Free certificate with free entry</Badge>}
  </>;
}

function OptionPrices({ option, pending }: { option: Attendance; pending?: boolean }) {
  if (pending) return <div className="badges"><Badge color="price-unknown">Price check pending</Badge></div>;
  return <div className="badges">
    {freeEntry(option) ? <Badge color="price-free">Free entry</Badge> :
      option.fee_inr === null ? <Badge color="price-unknown">Fee unannounced</Badge> :
        <Badge color={option.fee_inr ? "price-paid" : "price-unknown"}>{option.fee_inr ? rupees(option.fee_inr) + " entry" : "No registration fee"}</Badge>}
    {!!option.deposit_inr && <Badge color="price-paid">{rupees(option.deposit_inr)} deposit</Badge>}
    {option.certificate === "provided" ?
      option.certificate_fee_inr === 0 ? <Badge color={freeEntry(option) ? "price-free" : "price-unknown"}>
        <Award size={13} />{freeEntry(option) ? "Free certificate" : "No extra certificate fee"}</Badge> :
        option.certificate_fee_inr === null ? <Badge color="price-unknown">Certificate fee unannounced</Badge> :
          <Badge color="price-paid">{rupees(option.certificate_fee_inr)} certificate</Badge> :
        <Badge color="price-unknown">{option.certificate === "unavailable" ? "Certificate unavailable" : "Certificate unannounced"}</Badge>}
  </div>;
}

export function EventCard({ event, saved, onSave, compared, onCompare, showMaterials = false }: {
  event: Opportunity; saved?: boolean; onSave?: (event: Opportunity) => void;
  compared?: boolean; onCompare?: (event: Opportunity) => void;
  showMaterials?: boolean;
}) {
  const [tone, Icon] = theme(event);
  const modes = [...new Set(event.attendance.map((item) => item.mode))];
  return <article className="event-card" data-institute={instituteTheme(event.institute.category)}>
    <div className={"card-art " + tone}>
      <span className="card-category">{event.category}</span><Icon className="card-illustration" size={66} strokeWidth={1.25} />
      {onSave && <button className="icon-button bookmark" aria-label={saved ? "Remove bookmark" : "Bookmark event"} aria-pressed={saved} onClick={() => onSave(event)}><Bookmark size={17} fill={saved ? "currentColor" : "none"} /></button>}
    </div>
    <div className="card-content">
      <div className="card-host"><Building2 size={15} /> {event.institute.name} <span className="institute-tag">{event.institute.category}</span></div>
      <h3><Link href={"/events/" + event.id}>{event.title}</Link></h3>
      <div className="card-meta"><CalendarDays size={15} /> {dateLabel(event.starts)}{event.ends && event.ends !== event.starts && <> – {dateLabel(event.ends)}</>} {modes.length > 0 && <><span>·</span><span className="capitalize">{modes.join(" / ")}</span></>}</div>
      {showMaterials && <p className="card-deadline">
        <strong>Apply by</strong> {event.date_review_pending ? "Date check pending" : event.registration_deadline ? dateLabel(event.registration_deadline) : "Unannounced"}
      </p>}
      <div className="badges">
        {event.demo ? <Badge>Sample</Badge> : event.verification === "stale" ? <Badge>Source check due</Badge> : <Badge color="violet"><ShieldCheck size={13} /> Verified</Badge>}
        {event.new && <Badge color="new">New</Badge>}
        {event.date_review_pending && <Badge>Dates need recheck</Badge>}
        {showMaterials && event.status !== "scheduled" && <Badge><span className="capitalize">{event.status}</span></Badge>}
        {showMaterials && event.status === "scheduled" && !event.date_review_pending && !applicationOpen(event) && <Badge>Registration closed</Badge>}
        <PriceSummary event={event} />
      </div>
      {showMaterials && <div className="profile-event-files">
        <h4>Flyers &amp; posters</h4>
        {event.materials?.length ? <ul>{event.materials.map((material) => {
          const href = material.preview_url || material.official_url;
          const Icon = material.kind === "pdf" ? FileText : ImageIcon;
          return href && <li key={href}><a href={href} target="_blank" rel="noreferrer">
            <Icon size={15} /><span>{material.label}</span><ArrowUpRight size={13} />
          </a></li>;
        })}</ul> : <p>{event.demo ? "Sample event" : "No flyer or poster attached"}</p>}
      </div>}
      <div className="card-actions">
        {onCompare && <label><input type="checkbox" checked={compared ?? false} onChange={() => onCompare(event)} /> Compare</label>}
        <Link href={"/events/" + event.id}>View details <ArrowRight size={15} /></Link>
        {event.registration_url && applicationOpen(event) ?
          <a className={"card-apply" + (showMaterials ? " profile-apply" : "")} href={event.registration_url} target="_blank" rel="noreferrer">Apply <ArrowUpRight size={15} /></a> :
          event.source_url && <a className="card-apply" href={event.source_url} target="_blank" rel="noreferrer">Official announcement <ArrowUpRight size={15} /></a>}
      </div>
    </div>
  </article>;
}

function Filters({ values, change, institutes, clear, free }: {
  values: Record<string, string>; change: (key: string, value: string) => void; institutes: Institute[]; clear: () => void; free: boolean;
}) {
  const select = (key: string, title: string, placeholder: string, options: [string, string][]) =>
    <label className="filter"><span>{title}</span><select disabled={free && key === "max_fee"} value={values[key] ?? ""} onChange={(event) => change(key, event.target.value)}>
      <option value="">{placeholder}</option>{options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
    </select></label>;
  return <div className="filters">
    <label className="filter"><span>Institute</span><select value={values.institute ?? ""} onChange={(event) => change("institute", event.target.value)}>
      <option value="">All institutes</option>
      {sections.map((section) => <optgroup key={section.key} label={section.title}>
        <option value={section.key}>All {section.title}</option>
        {institutes.filter((host) => host.category === section.key).map((host) => <option key={host.id} value={host.id}>{host.name}</option>)}
      </optgroup>)}
    </select></label>
    {select("category", "Category", "All categories", categories.map((category) => [category, category]))}
    {select("max_fee", "Price", "Any price", [["0", "Free ₹0"], ["500", "Up to ₹500"], ["1000", "Up to ₹1,000"]])}
    {select("mode", "Participation", "Any mode", [["online", "Online"], ["offline", "Offline"], ["hybrid", "Hybrid"]])}
    {select("certificate", "Certificate", "Any status", [["free", "Free certificate"], ["available", "Certificate available"]])}
    {select("level", "Academic level", "Any level", levels)}
    {select("before", "Starts within", "Any date", [["7", "7 days"], ["30", "30 days"], ["90", "90 days"]])}
    <button className="text-button reset" onClick={clear}>Clear filters</button>
  </div>;
}

function TrustStrip() {
  return <div className="trust-strip"><div className="wrap">
    <span><ShieldCheck size={18} /> Official institute sources</span><span><Award size={18} /> Free and paid options</span><span><CalendarDays size={18} /> Dates and deadlines at a glance</span>
  </div></div>;
}

function InstituteSections({ institutes, onDirectory = false }: { institutes: Institute[]; onDirectory?: boolean }) {
  return <nav className="institute-nav" aria-label="Institute sections">
    {sections.map(({ key, slug, title, name, Icon }) => {
      const count = institutes.filter((institute) => institute.category === key).length;
      return <Link className="institute-link" data-institute={slug} key={key} href={(onDirectory ? "" : "/institutes") + "#" + slug}>
        <span className="directory-icon"><Icon size={20} /></span>
        <span><strong>{title}</strong><small>{count ? count + (key === "Central University" ? " universities" : " institutes") : name}</small></span>
        <ArrowRight size={16} aria-hidden="true" />
      </Link>;
    })}
  </nav>;
}

function Finder({ route, institutes, profile, saved, onSave, compared, onCompare }: {
  route: string; institutes: Institute[]; profile: Profile | null; saved: Set<string>; onSave: (event: Opportunity) => void;
  compared: Opportunity[]; onCompare: (event: Opportunity) => void;
}) {
  const router = useRouter();
  const home = !route;
  const free = route === "freelearn";
  const [values, setValues] = useState<Record<string, string>>(free ? { max_fee: "0" } : {});
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Opportunity[]>([]);
  const [total, setTotal] = useState(0);
  const [preview, setPreview] = useState(false);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [locked, setLocked] = useState(false);
  const [initial, setInitial] = useState(false);
  const pageSize = home ? 3 : 9;

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setQuery(params.get("q") ?? "");
    setValues({ ...(free ? { max_fee: "0" } : {}), ...(params.has("institute") ? { institute: params.get("institute") ?? "" } : {}) });
    setInitial(true);
  }, [free]);

  useEffect(() => {
    if (!initial) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
    for (const [key, value] of Object.entries(values)) {
      if (!value) continue;
      if (key === "before") {
        const until = new Date(indianToday() + "T00:00:00Z");
        until.setUTCDate(until.getUTCDate() + Number(value));
        params.set(key, until.toISOString().slice(0, 10));
      } else params.set(key, value);
    }
    if (query.trim()) params.set("q", query.trim());
    const timeout = setTimeout(async () => {
      setLoading(true); setError(""); setLocked(false);
      try {
        const data = await api<{ events: Opportunity[]; preview: boolean; total: number }>("/events?" + params, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setResults(data.events); setTotal(data.total); setPreview(data.preview);
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        setResults([]); setTotal(0);
        if (error instanceof ApiError && error.status === 401) setLocked(true);
        else setError(errorText(error));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 180);
    return () => { clearTimeout(timeout); controller.abort(); };
  }, [values, query, offset, initial, pageSize, profile?.email]);

  return <>
    {home && <><Hero onSearch={(text) => router.push("/explore" + (text.trim() ? "?q=" + encodeURIComponent(text.trim()) : ""))} /><TrustStrip />
      <section className="wrap home-institutes" aria-labelledby="browse-institutes"><h2 id="browse-institutes">Browse by institute type</h2><InstituteSections institutes={institutes} /></section></>}
    <section className={home ? "wrap event-section" : "wrap inner-page"}>
      {home ? <div className="section-head"><div><div className="eyebrow"><span /> THE EVENT FINDER</div><h2>Explore opportunities</h2><p>Find events that fit your interests, budget and schedule.</p></div>
        <Link href="/explore" className="button outline">View all events <ArrowRight size={16} /></Link></div> :
        <Heading label={free ? "FREELEARN" : "THE EVENT FINDER"} title={free ? "Learn for free." : "Explore opportunities"} text={free ? "Find events with free participation and check which ones include a free certificate." : "Search workshops, hackathons, conferences and more."} />}
      {!home && <div className="finder-search"><Search size={20} /><input aria-label="Search catalog" value={query} placeholder="Search by topic, event or institute" onChange={(event) => { setQuery(event.target.value); setOffset(0); }} /></div>}
      <Filters values={values} institutes={institutes} free={free} change={(key, value) => { setValues((previous) => ({ ...previous, [key]: value })); setOffset(0); }}
        clear={() => { setValues(free ? { max_fee: "0" } : {}); setQuery(""); setOffset(0); }} />
      <div className="result-row"><span><ListFilter size={16} /> {loading ? "Loading opportunities…" : preview ? "Public preview" : total + (total === 1 ? " opportunity" : " opportunities")}</span>
        {results.some((event) => event.demo) && <span className="sample-note">Sample listings · fictional events</span>}
      </div>
      {error && <Notice error>{error}</Notice>}
      {locked && <Notice><Link href="/my-ajora">Sign in or join AJORA</Link> to search and filter the full catalog.</Notice>}
      {!loading && !locked && !error && !results.length && <div className="empty"><Compass size={32} /><h3>No opportunities found</h3><p>Try a different keyword or clear a filter.</p></div>}
      {results.length > 0 && <div className="card-grid">{results.map((event) =>
        <EventCard key={event.id} event={event} saved={saved.has(event.id)} onSave={onSave} compared={compared.some((item) => event.id === item.id)} onCompare={onCompare} />)}</div>}
      {!home && total > pageSize && <div className="pagination"><button className="button outline" onClick={() => setOffset((old) => old - pageSize)} disabled={!offset}>Previous</button><span>{offset + 1}–{Math.min(offset + pageSize, total)} of {total}</span><button className="button outline" onClick={() => setOffset((old) => old + pageSize)} disabled={offset + pageSize >= total}>Next</button></div>}
      {home && <div className="free-banner"><div className="free-icon"><Award size={30} /></div><div><h3>Looking for free learning?</h3><p>Browse ₹0 events and check certificate costs before you apply.</p></div><Link href="/freelearn" className="button dark">Explore FreeLearn <ArrowRight size={16} /></Link></div>}
    </section>
  </>;
}

function InstituteDirectory({ institutes, error }: { institutes: Institute[]; error: string }) {
  const [query, setQuery] = useState("");
  const [iiitSubgroup, setIiitSubgroup] = useState("");
  const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const matching = institutes.filter((item) => terms.every((term) =>
    [item.name, item.category, item.city, item.state, ...item.domains].join(" ").toLowerCase().includes(term)));
  const upcoming = matching.filter((item) => item.upcoming_events);
  useEffect(() => {
    if (!institutes.length) return;
    const section = sections.find(({ slug }) => window.location.hash === "#" + slug);
    if (section) document.getElementById(section.slug)?.scrollIntoView();
  }, [institutes]);
  return <section className="wrap inner-page">
    <Heading label="INSTITUTE DIRECTORY" title="Find an institute." text="Search by institute, city or state. Institutes with upcoming and recently listed events appear first in each section." />
    <div className="finder-search directory-search"><Search size={20} /><input aria-label="Search institutes" value={query} placeholder="Search institutes, cities or states" onChange={(event) => { setQuery(event.target.value); setIiitSubgroup(""); }} />
      {query && <button className="icon-button" aria-label="Clear institute search" onClick={() => setQuery("")}><X size={17} /></button>}</div>
    <InstituteSections institutes={matching} onDirectory />
    {error && <Notice error>{error}</Notice>}
    {!error && !institutes.length && <Notice>Loading institutes…</Notice>}
    {upcoming.length > 0 && <div className="upcoming-hosts"><h2><CalendarDays size={21} /> Institutes with upcoming events</h2>
      <div>{upcoming.slice(0, 8).map((item) => <Link className="institute-tag" data-institute={instituteTheme(item.category)} key={item.id} href={"/explore?institute=" + item.id}>{item.name} <ArrowRight size={14} /></Link>)}</div>
    </div>}
    {terms.length > 0 && <p className="directory-results" role="status">{matching.length} matching {matching.length === 1 ? "institute" : "institutes"}</p>}
    {sections.map(({ key, slug, title, name, Icon }) => {
      const hosts = matching.filter((item) => item.category === key && (key !== "IIIT" || !iiitSubgroup || item.subgroup === iiitSubgroup));
      return <section id={slug} className="directory-section" data-institute={slug} aria-labelledby={slug + "-title"} key={key}>
        <div className="directory-section-head"><span className="directory-icon"><Icon size={24} /></span><div><h2 id={slug + "-title"}>{title}</h2><p>{name}</p></div>
          <span className="institute-tag">{hosts.length}</span>
          <Link className="button outline" href={"/explore?institute=" + encodeURIComponent(key)}>Browse events <ArrowRight size={15} /></Link></div>
        {key === "IIIT" && <div className="tabs institute-subgroups" aria-label="IIIT subsections">
          {["", ...iiitSubgroups].map((subgroup) =>
            <button key={subgroup} type="button" className={iiitSubgroup === subgroup ? "active" : ""} aria-pressed={iiitSubgroup === subgroup} onClick={() => setIiitSubgroup(subgroup)}>{subgroup || "All IIITs"}</button>)}
        </div>}
        {hosts.length ? <div className="card-grid">{hosts.map((item) => {
          const count = item.upcoming_events || item.recent_events || item.sample_events || 0;
          const status = item.upcoming_events ? count + " upcoming" : item.recent_events ? count + " in the past year" : item.sample_events ? count + (count === 1 ? " sample event" : " sample events") : null;
          return <article className={"directory-card" + (count ? " has-events" : "")} key={item.id}>
            <div className="directory-card-head">
              <Link href={"/institutes/" + item.id} className="institute-profile-link">
                <InstituteLogo institute={item} />
                <h3>{item.name}</h3>
              </Link>
              <span className="institute-tag"><CalendarDays size={13} /> {item.upcoming_events ?? 0} upcoming</span>
              {!item.upcoming_events && status && <span className="institute-tag">{status}</span>}
            </div>
            {(item.city || item.state) && <p><MapPin size={15} /> {[item.city, item.state].filter(Boolean).join(", ")}</p>}
            {item.subgroup && <p className="directory-subgroup">{item.subgroup}</p>}
            <div className="directory-card-links">
              <Link href={"/institutes/" + item.id}>View profile <ArrowRight size={15} /></Link>
              <Link href={"/institutes/" + item.id + "#institute-events"}>View events <CalendarDays size={14} /></Link>
              {(item.official_url || item.domains[0]) && <a href={item.official_url || "https://" + item.domains[0]} target="_blank" rel="noreferrer">Official site <ArrowUpRight size={15} /></a>}
            </div>
          </article>;
        })}</div> : <p className="directory-empty">{terms.length || (key === "IIIT" && iiitSubgroup) ? "No matches in this section." : "No institutes to display."}</p>}
      </section>;
    })}
  </section>;
}

function InstituteLogo({ institute, main = false }: { institute: Institute; main?: boolean }) {
  const [unavailable, setUnavailable] = useState(false);
  const showImage = !!institute.logo_url && !unavailable;
  return <span className={main ? "institute-logo institute-logo-main" : "institute-logo"}>
    {showImage ? <img src={institute.logo_url} alt={main ? "Official logo of " + institute.name : ""} loading={main ? "eager" : "lazy"} onError={() => setUnavailable(true)} />
      : <Landmark size={main ? 36 : 21} aria-hidden="true" />}
  </span>;
}

function InstituteView({ id }: { id: string }) {
  const [institute, setInstitute] = useState<Institute | null>(null);
  const [events, setEvents] = useState<Opportunity[]>([]);
  const [error, setError] = useState("");
  const [eventError, setEventError] = useState("");
  const [loadingEvents, setLoadingEvents] = useState(true);
  const [archive, setArchive] = useState(false);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [publishedCount, setPublishedCount] = useState(0);
  const pageSize = 12;
  useEffect(() => {
    const controller = new AbortController();
    const encoded = encodeURIComponent(id);
    api<Institute>("/institutes/" + encoded, { signal: controller.signal }).then(setInstitute).catch((error) => {
      if (!controller.signal.aborted) setError(errorText(error));
    });
    return () => controller.abort();
  }, [id]);
  useEffect(() => {
    const controller = new AbortController();
    setLoadingEvents(true);
    setEventError("");
    const path = "/institutes/" + encodeURIComponent(id) + "/events?archive=" + archive + "&limit=" + pageSize + "&offset=" + offset;
    api<{ events: Opportunity[]; has_more: boolean; published_count: number }>(path, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) {
        setEvents(result.events);
        setHasMore(result.has_more);
        setPublishedCount(result.published_count);
      }
    }).catch((error) => { if (!controller.signal.aborted) setEventError(errorText(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoadingEvents(false); });
    return () => controller.abort();
  }, [id, archive, offset]);
  useEffect(() => {
    if (institute && window.location.hash === "#institute-events") document.getElementById("institute-events")?.scrollIntoView();
  }, [institute]);
  return <section className="wrap inner-page">
    <Link className="back" href="/institutes"><ArrowLeft size={16} /> Institute directory</Link>
    {error && <Notice error>{error}</Notice>}
    {!institute && !error && <Notice>Loading institute…</Notice>}
    {institute && <>
      <header className="institute-profile" data-institute={instituteTheme(institute.category)}>
        <InstituteLogo institute={institute} main />
        <div><div className="badges"><span className="institute-tag">{institute.category}</span>
          {institute.subgroup && <span className="institute-tag">{institute.subgroup}</span>}</div>
          <h1>{institute.name}</h1>
          <p><MapPin size={17} /> {[...new Set([institute.city, institute.state].filter(Boolean))].join(", ")}</p>
        </div>
      </header>
      <div className="institute-profile-content">
        <h2>About the institute</h2><p>{institute.introduction || [institute.city, institute.state].filter(Boolean).join(", ")}</p>
        <div className="panel-actions">
          {institute.official_url && <a className="button purple" href={institute.official_url} target="_blank" rel="noreferrer">Official website <ArrowUpRight size={16} /></a>}
          {institute.about_url && institute.about_url !== institute.official_url && <a className="button outline" href={institute.about_url} target="_blank" rel="noreferrer">Institute overview <ArrowUpRight size={16} /></a>}
          <a className="button outline" href={institute.registry_url} target="_blank" rel="noreferrer">Directory source <ArrowUpRight size={16} /></a>
        </div>
        {institute.logo_source && <a className="logo-source" href={institute.logo_source} target="_blank" rel="noreferrer">Logo source <ArrowUpRight size={13} /></a>}
        {institute.website_status && <p className="website-status">{institute.website_status === "reachable" ? "Official homepage reached" : institute.website_status === "official_confirmed" ? "Official homepage confirmed independently" : "Official homepage did not respond to the last check"}
          {institute.website_checked_on && " · " + dateLabel(institute.website_checked_on)}</p>}
      </div>
      <div className="section-head institute-events-heading" id="institute-events"><div><h2>Events &amp; flyers</h2></div>
        {!loadingEvents && !eventError && <span className="institute-tag">{publishedCount} published {archive ? "past or updated" : "upcoming"} {publishedCount === 1 ? "event" : "events"}</span>}
      </div>
      {institute.event_monitor && <p className="website-status">
        {{ checked: "Official event sources checked", pending: "Event source check pending",
           unavailable: "Official event sources unavailable at the last check",
           partial: "Some official event sources still need a successful check" }[institute.event_monitor.status]}
        {institute.event_monitor.last_success && " · Last successful check " + dateLabel(institute.event_monitor.last_success)}
        {institute.event_monitor.pending > 0 && " · " + institute.event_monitor.pending + " sources awaiting activation"}
      </p>}
      <div className="tabs" aria-label="Institute event history">
        {[false, true].map((past) => <button key={String(past)} type="button" className={past === archive ? "active" : ""} aria-pressed={past === archive}
          onClick={() => { setArchive(past); setOffset(0); }}>{past ? "Past events & updates" : "Upcoming events"}</button>)}
      </div>
      {eventError ? <Notice error>{eventError}</Notice> : loadingEvents ? <Notice>Loading events…</Notice> : !events.length ?
        <div className="empty"><CalendarDays size={26} /><h3>{archive ? "No past events or updates listed" :
          institute.event_monitor?.status === "checked" ? "No upcoming events published from checked sources" : "No verified upcoming listings yet"}</h3>
          <p>Visit the institute’s website for its latest announcements.</p></div> :
          <div className="card-grid">{events.map((event) => <EventCard key={event.id} event={event} showMaterials />)}</div>}
      {!loadingEvents && !eventError && (offset > 0 || hasMore) && <div className="pagination">
        <button className="button outline" disabled={offset === 0} onClick={() => { setOffset(offset - pageSize); document.getElementById("institute-events")?.scrollIntoView(); }}>Previous</button>
        <span>Page {1 + offset / pageSize}</span>
        <button className="button outline" disabled={!hasMore} onClick={() => { setOffset(offset + pageSize); document.getElementById("institute-events")?.scrollIntoView(); }}>Next</button>
      </div>}
    </>}
  </section>;
}

function EventDetail({ id, onSave, saved }: { id: string; onSave: (event: Opportunity) => void; saved: Set<string> }) {
  const [event, setEvent] = useState<Opportunity | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<Opportunity>("/events/" + encodeURIComponent(id), { signal: controller.signal }).then(setEvent).catch((error) => {
      if (!controller.signal.aborted) setError(errorText(error));
    });
    return () => controller.abort();
  }, [id]);
  if (!event) return <section className="wrap inner-page"><Link className="back" href="/explore"><ArrowLeft size={16} /> Explore events</Link><Notice error={!!error}>{error || "Loading event…"}</Notice></section>;
  const closed = !!event.registration_deadline && event.registration_deadline < indianToday();
  const canApply = !!event.registration_url && applicationOpen(event);
  return <section className="wrap inner-page"><Link className="back" href="/explore"><ArrowLeft size={16} /> Explore events</Link>
    <div className="detail-grid">
      <article className="detail-main"><div className="badges"><Badge color="violet">{event.category}</Badge><Badge>{event.demo ? "Sample event" : event.verification === "stale" ? "Source check due" : "Verified source"}</Badge>{event.status !== "scheduled" && <Badge>{event.status}</Badge>}</div>
        <h1>{event.title}</h1><p className="detail-host"><Building2 size={17} /> <Link href={"/institutes/" + event.institute.id}>{event.institute.name}</Link> · {event.institute.city}</p>
        {event.demo && <Notice>This fictional listing illustrates how an event appears on AJORA.</Notice>}
        {event.description && <p>{event.description}</p>}
        <section className="event-materials" id="flyers-posters" aria-labelledby="materials-title">
          <h2 id="materials-title">Flyers &amp; posters</h2>
          {event.materials?.length ? <div className="material-grid">{event.materials.map((material, index) => {
            const Icon = material.kind === "pdf" ? FileText : ImageIcon;
            const href = material.official_url || material.preview_url;
            if (!href) return null;
            return <article className="material-card" key={href + index}>
              {material.preview_url && <a className="material-preview" href={material.preview_url} target="_blank" rel="noreferrer" aria-label={"View full-size " + material.label.toLowerCase()}>
                <img src={material.preview_url} alt={material.label + " for " + event.title} loading="lazy" />
              </a>}
              <div className="material-info"><Icon size={23} /><div><h3>{material.label}</h3>
                <a href={href} target="_blank" rel="noreferrer" aria-label={"Open " + material.label}>{material.kind === "pdf" ? "Open PDF" : material.official_url ? "Open institute image" : "Open full-size poster"} <ArrowUpRight size={14} /></a>
              </div></div>
            </article>;
          })}</div> : <p>{event.demo ? "Sample events have no official flyers or posters." : "No flyer or poster has been attached to this event yet."}</p>}
        </section>
        {event.learning_outcomes && <><h2>Learning outcomes</h2><p>{event.learning_outcomes}</p></>}
        <h2>Participation options</h2>
        {!event.attendance.length && <p>Participation details are unannounced.</p>}
        {event.attendance.map((option, index) => <div className="attendance" key={index}><h3 className="capitalize">{option.mode}</h3>
          {option.levels.length > 0 && <p>{option.levels.map((level) => levelLabels.get(level) ?? level).join(", ")}</p>}
          <OptionPrices option={option} pending={event.price_review_pending} />
          {option.conditions && <p>{option.conditions}</p>}{option.certificate_conditions && <p>{option.certificate_conditions}</p>}
        </div>)}
        {event.eligibility_notes && <><h2>Eligibility</h2><p>{event.eligibility_notes}</p></>}
        {event.assistance && <><h2>Assistance</h2><p>{event.assistance}</p></>}
        {event.entities.length > 0 && <><h2>People and organizations</h2><ul>{event.entities.map((entity, index) => <li key={index}>{entity.name} · {entity.role}{entity.details && " · " + entity.details}</li>)}</ul></>}
        {event.evidence?.length ? <details><summary>Field evidence</summary><dl>{event.evidence.map((item) => <div key={item.field}><dt>{item.field.replaceAll("_", " ")}</dt><dd>{item.excerpt}</dd></div>)}</dl></details> : null}
      </article>
      <aside className="detail-aside"><h2>At a glance</h2><dl>
        <div><dt>Event date</dt><dd>{dateLabel(event.starts)}{event.ends && event.ends !== event.starts ? " – " + dateLabel(event.ends) : ""}{event.date_review_pending && " · Recheck pending"}</dd></div>
        <div><dt>Registration deadline</dt><dd>{event.date_review_pending ? "Date check pending" : event.registration_deadline ? dateLabel(event.registration_deadline) : "Unannounced"} {closed && !event.date_review_pending && "· Closed"}</dd></div>
        {event.venue && <div><dt>Venue</dt><dd>{event.venue}</dd></div>}
        {event.checked_at && !event.demo && <div><dt>Last verified</dt><dd>{dateLabel(event.checked_at)}</dd></div>}
      </dl>
      {canApply && <a className="button purple" href={event.registration_url!} target="_blank" rel="noreferrer">Apply on {event.registration_external ? "registration form" : "institute site"} <ArrowUpRight size={16} /></a>}
      {canApply && event.registration_external && <p className="application-host">Linked by the institute · {new URL(event.registration_url!).hostname}</p>}
      {event.source_url && <a className={"button " + (canApply ? "outline" : "purple")} href={event.source_url} target="_blank" rel="noreferrer">Official announcement <ArrowUpRight size={16} /></a>}
      {event.source_url && !event.registration_url && applicationOpen(event) && <p className="application-host">Check the announcement for registration instructions.</p>}
      {!!event.materials?.length && <a className="button outline" href="#flyers-posters"><FileText size={16} /> Flyers &amp; posters <span>({event.materials.length})</span></a>}
      <button className="button outline" onClick={() => onSave(event)}><Bookmark size={16} fill={saved.has(event.id) ? "currentColor" : "none"} /> {saved.has(event.id) ? "Saved to My AJORA" : "Save event"}</button>
      {!event.demo && <Link href={"/organizer?event=" + event.id}>Report a correction</Link>}</aside>
    </div>
  </section>;
}

function Comparison({ items, onClose }: { items: Opportunity[]; onClose: () => void }) {
  return <div className="compare-panel" role="region" aria-label="Compare events"><div className="wrap"><div className="compare-top"><h3>Compare events</h3><button className="icon-button" aria-label="Close comparison" onClick={onClose}><X size={20} /></button></div>
    <div className="compare-grid">{items.map((event) => <div key={event.id}><Link href={"/events/" + event.id}>{event.title}</Link><p>{event.institute.name} · {dateLabel(event.starts)}</p>
      {event.attendance.map((option, index) => <div key={index}><p className="capitalize">{option.mode}</p><OptionPrices option={option} pending={event.price_review_pending} /></div>)}
    </div>)}</div></div></div>;
}

function AboutAjora() {
  return <section className="wrap inner-page about-page">
    <Heading label="ABOUT AJORA" title="Why I built AJORA" />
    <div className="institute-profile-content about-copy">
      <p>I built AJORA because finding a workshop, hackathon, or academic event often means searching institute websites, PDF notices, and posters one by one. It’s easy to miss a deadline or find out too late that there’s a fee.</p>
      <p>I wanted students across India to have one place to find these opportunities, compare costs, and go straight to the institute’s registration page. Free events and free certificates should be easy to spot when they’re available.</p>
      <h2>How AJORA checks listings</h2>
      <p>AJORA looks for events on official institute sources. The extraction workflow keeps the event date separate from the application deadline, preserves the source announcement, and sends dates, fees, flyers, and registration links for review before publication.</p>
      <h2>Help make AJORA better</h2>
      <p>Tell us what would make it easier to find or apply for an event.</p>
      <div className="panel-actions"><Link className="button purple" href="/explore">Explore events <ArrowRight size={16} /></Link><Link className="button outline" href="/feedback">Share feedback <ArrowRight size={16} /></Link></div>
    </div>
  </section>;
}

export function Ajora({ route }: { route: string[] }) {
  const key = route[0] ?? "";
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [institutes, setInstitutes] = useState<Institute[]>([]);
  const [directoryError, setDirectoryError] = useState("");
  const [compared, setCompared] = useState<Opportunity[]>([]);
  const [comparing, setComparing] = useState(false);
  const [message, setMessage] = useState("");

  async function syncProfile() {
    try { setProfile(await api<Profile>("/account/me")); }
    catch (error) {
      if (error instanceof ApiError && error.status === 401) setProfile(null);
    }
  }
  useEffect(() => {
    void syncProfile();
    api<Institute[]>("/institutes").then(setInstitutes).catch((error) => setDirectoryError(errorText(error)));
  }, []);
  useEffect(() => {
    if (!message) return;
    const id = setTimeout(() => setMessage(""), 4200);
    return () => clearTimeout(id);
  }, [message]);
  const saved = new Set(profile?.saved.map((event) => event.id));
  async function toggleSave(event: Opportunity) {
    if (!profile) { router.push("/my-ajora"); return; }
    try {
      await api("/account/saved/" + event.id, { method: saved.has(event.id) ? "DELETE" : "PUT" });
      await syncProfile();
      setMessage(saved.has(event.id) ? "Removed from saved events" : "Saved to My AJORA");
    } catch (error) { setMessage(errorText(error)); }
  }
  function toggleCompare(event: Opportunity) {
    if (compared.some((item) => event.id === item.id)) setCompared(compared.filter((item) => event.id !== item.id));
    else if (compared.length < 3) setCompared([...compared, event]);
    else setMessage("Choose up to three events to compare.");
  }

  return <><Header route={key} profile={profile} /><main id="main">
    {(key === "" || key === "explore" || key === "freelearn") && <Finder key={key} route={key} institutes={institutes} profile={profile} saved={saved} onSave={toggleSave} compared={compared} onCompare={toggleCompare} />}
    {key === "institutes" && (route[1] ? <InstituteView key={route[1]} id={route[1]} /> : <InstituteDirectory institutes={institutes} error={directoryError} />)}
    {key === "events" && <EventDetail key={route[1]} id={route[1]} onSave={toggleSave} saved={saved} />}
    {key === "feedback" && <Feedback key={route[1] ?? "board"} id={route[1]} profile={profile} />}
    {key === "about" && <AboutAjora />}
    {key === "my-ajora" && <Account profile={profile} institutes={institutes} onRefresh={syncProfile} onSave={toggleSave} />}
    {key === "organizer" && <Organizer profile={profile} institutes={institutes} />}
    {key === "review" && <ReviewDesk />}
  </main><Footer />
    {compared.length > 0 && (comparing ? <Comparison items={compared} onClose={() => setComparing(false)} /> : <div className="compare-tray"><button className="button purple" onClick={() => setComparing(true)}>Compare {compared.length} {compared.length === 1 ? "event" : "events"} <ArrowUpRight size={16} /></button><button className="icon-button" aria-label="Clear comparison" onClick={() => setCompared([])}><X size={16} /></button></div>)}
    {message && <div className="toast" role="status">{message}</div>}
  </>;
}
