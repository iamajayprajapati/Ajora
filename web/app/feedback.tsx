"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, MessageCircle, Plus, Send, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api, dateLabel, errorText, type Profile } from "./client";
import { Heading, Notice } from "./site";

type Post = { id: number; title: string | null; body: string; category: string | null; removed: boolean; created_at: string; can_remove: boolean };
type Topic = Post & { reply_count: number };
type Board = { topics: Topic[]; has_more: boolean };
type Discussion = { topic: Topic; replies: Post[]; has_more: boolean };

const topicTitle = (topic: Topic) => topic.title ?? "Removed discussion";
const repliesLabel = (count: number) => count + (count === 1 ? " reply" : " replies");
const signInPath = "/my-ajora?from=feedback";

function PostMeta({ post }: { post: Post }) {
  return <div className="feedback-meta"><span>{post.can_remove ? "You" : post.removed ? "Removed" : "AJORA member"}</span>
    <time dateTime={post.created_at}>{dateLabel(post.created_at)}</time></div>;
}

export function Feedback({ id, profile }: { id?: string; profile: Profile | null }) {
  const router = useRouter();
  const [board, setBoard] = useState<Board | null>(null);
  const [discussion, setDiscussion] = useState<Discussion | null>(null);
  const [composer, setComposer] = useState(false);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const [draft, setDraft] = useState("");
  const size = id ? 25 : 20;

  useEffect(() => {
    const controller = new AbortController();
    const path = "/feedback" + (id ? "/" + encodeURIComponent(id) : "") + "?offset=" + offset;
    if (id) setDiscussion(null);
    else setBoard(null);
    const request = id ? api<Discussion>(path, { signal: controller.signal }).then(setDiscussion) :
      api<Board>(path, { signal: controller.signal }).then(setBoard);
    request.catch((error) => {
      if (!controller.signal.aborted) setError(errorText(error));
    });
    return () => controller.abort();
  }, [id, offset, refresh, profile?.email]);

  useEffect(() => {
    if (discussion && window.location.hash === "#replies") document.getElementById("replies")?.scrollIntoView();
  }, [!!discussion]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const data = id ? { body: draft } : { title: fields.get("title"), category: fields.get("category"), body: draft };
    setBusy(true); setError(""); setResult("");
    try {
      const post = await api<{ id: number }>(id ? "/feedback/" + id + "/replies" : "/feedback", {
        method: "POST", body: JSON.stringify(data),
      });
      setDraft("");
      if (id) {
        setOffset(0);
        setRefresh((value) => value + 1);
        setResult("Reply posted.");
      } else {
        router.push("/feedback/" + post.id);
      }
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  async function remove(post: Post) {
    if (!window.confirm("Remove your " + (post.title ? "discussion" : "reply") + " from the public forum?")) return;
    setBusy(true); setError(""); setResult("");
    try {
      await api("/feedback/posts/" + post.id, { method: "DELETE" });
      if (discussion?.topic.id === post.id && !discussion.topic.reply_count) {
        router.push("/feedback");
      } else {
        setOffset(0);
        setRefresh((value) => value + 1);
        setResult("Post removed.");
      }
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  const more = id ? discussion?.has_more : board?.has_more;
  return <section className="wrap inner-page feedback-page">
    {id ? <Link className="back" href="/feedback"><ArrowLeft size={16} /> All discussions</Link> :
      <Heading label="COMMUNITY FEEDBACK" title="AJORA feedback" text="Share suggestions, report issues, or tell us what’s working.">
        {profile ? <button className="button purple" aria-expanded={composer} aria-controls="feedback-composer"
          onClick={() => setComposer((open) => !open)}><Plus size={17} /> New discussion</button> :
          <Link className="button purple" href={signInPath}>Sign in to post <ArrowRight size={16} /></Link>}
      </Heading>}

    {error && <Notice error>{error}</Notice>}{result && <Notice>{result}</Notice>}
    {!id && composer && profile && <form id="feedback-composer" className="form-panel feedback-composer" onSubmit={submit}>
      <h2>Start a discussion</h2>
      <label>Title<input name="title" minLength={5} maxLength={120} required autoFocus /></label>
      <label>Category<select name="category" defaultValue="Suggestion"><option>Suggestion</option><option>Issue</option><option>General</option></select></label>
      <label>Your feedback<textarea name="body" minLength={3} maxLength={2000} rows={5} value={draft} onChange={(event) => setDraft(event.target.value)} required /></label>
      <small>Posts are public. Keep personal information out of your message.</small>
      <button className="button purple" disabled={busy}><Send size={16} /> Post discussion</button>
    </form>}

    {!id && board?.topics.length === 0 && <div className="empty"><MessageCircle size={28} /><h3>No discussions yet</h3><p>Start with a suggestion or an issue you noticed.</p></div>}
    {!id && !!board?.topics.length && <div className="feedback-list">{board.topics.map((topic) =>
      <article className="feedback-card" key={topic.id}>
        <div className="feedback-card-head"><span className="feedback-tag">{topic.category || "General"}</span><PostMeta post={topic} /></div>
        <h2><Link href={"/feedback/" + topic.id}>{topicTitle(topic)} <ArrowRight size={15} /></Link></h2>
        {topic.body && <p className="feedback-excerpt">{topic.body}</p>}
        <Link className="feedback-replies" href={"/feedback/" + topic.id + "#replies"}><MessageCircle size={16} /> {repliesLabel(topic.reply_count)}</Link>
      </article>)}</div>}

    {id && discussion && <>
      <article className="feedback-card feedback-topic">
        <div className="feedback-card-head"><span className="feedback-tag">{discussion.topic.category || "General"}</span><PostMeta post={discussion.topic} /></div>
        <h1>{topicTitle(discussion.topic)}</h1>
        <p className="feedback-body">{discussion.topic.removed ? "The original post was removed." : discussion.topic.body}</p>
        {discussion.topic.can_remove && <button className="feedback-remove" disabled={busy} onClick={() => remove(discussion.topic)}><Trash2 size={15} /> Remove my post</button>}
      </article>
      <section className="feedback-thread" id="replies" aria-labelledby="feedback-replies-title">
        <h2 id="feedback-replies-title">{repliesLabel(discussion.topic.reply_count)}</h2>
        {!discussion.topic.removed && (profile ? <form className="form-panel feedback-composer" onSubmit={submit}>
          <label>Your reply<textarea name="body" minLength={3} maxLength={2000} required rows={3} value={draft} onChange={(event) => setDraft(event.target.value)} /></label>
          <small>Replies appear publicly.</small>
          <button className="button purple" disabled={busy}><Send size={16} /> Post reply</button>
        </form> : <Notice><Link href={"/my-ajora?from=feedback/" + discussion.topic.id}>Sign in</Link> to join the discussion.</Notice>)}
        <div className="feedback-list">{discussion.replies.map((reply) =>
          <article className="feedback-card feedback-reply" key={reply.id}>
            <PostMeta post={reply} />
            <p className="feedback-body">{reply.body}</p>
            {reply.can_remove && <button className="feedback-remove" disabled={busy} onClick={() => remove(reply)}><Trash2 size={15} /> Remove my reply</button>}
          </article>)}</div>
      </section>
    </>}

    {!error && (id ? !discussion : !board) && <Notice>Loading discussions…</Notice>}
    {(offset > 0 || more) && <div className="pagination">
      <button className="button outline" disabled={!offset} onClick={() => setOffset((value) => Math.max(0, value - size))}>Previous</button>
      <span>Page {1 + offset / size}</span>
      <button className="button outline" disabled={!more} onClick={() => setOffset((value) => value + size)}>Next</button>
    </div>}
  </section>;
}
