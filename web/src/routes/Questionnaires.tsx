import { useEffect, useMemo, useRef, useState } from "react";
import { api, type QuestionnaireSummary, type QuestionnaireDetail, type QuestionnaireItem } from "../lib/api";
import { parseExcel, writeBackAndDownload, colLetter, type ExcelParseResult } from "../voice/excelHelpers";

export function Questionnaires() {
  const [list,       setList]       = useState<QuestionnaireSummary[] | null>(null);
  const [templates,  setTemplates]  = useState<{ key: string; name: string; question_count: number }[] | null>(null);
  const [openId,     setOpenId]     = useState<string | null>(null);
  const [showNew,    setShowNew]    = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  // Per-questionnaire excel cache so the editor view can write back to the
  // original workbook.
  const excelCache = useRef<Map<string, ExcelParseResult>>(new Map());
  const [err,       setErr]       = useState<string | null>(null);

  async function reload() {
    setList(null);
    setErr(null);
    try {
      const [l, t] = await Promise.all([
        api.listQuestionnaires(),
        api.listQuestionnaireTemplates(),
      ]);
      setList(l.questionnaires);
      setTemplates(t.templates);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setList([]);
    }
  }

  useEffect(() => { reload(); }, []);

  return (
    <div className="max-w-6xl">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <h1 className="text-3xl font-bold tracking-tight">Questionnaires</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setShowUpload(true)}
            title="Upload a customer's vendor security questionnaire (.xlsx)"
            className="px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-medium transition"
          >
            ⬆️ Upload Excel
          </button>
          <button
            onClick={() => setShowNew(true)}
            className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition"
          >
            + Start questionnaire
          </button>
        </div>
      </div>
      <p className="text-slate-600 mt-1">
        Upload a customer's Excel questionnaire, or start one of our built-in
        banks. Answers are auto-filled from your live findings; click ✨ on any
        item for an AI-drafted answer + justification.
      </p>

      {err && <div className="mt-4 p-3 rounded-lg bg-red-50 text-red-700 text-sm">{err}</div>}

      <div className="mt-6 rounded-2xl border border-slate-200 bg-white overflow-hidden">
        {list === null ? (
          <p className="text-slate-500 p-6 text-sm">Loading…</p>
        ) : list.length === 0 ? (
          <div className="p-8 text-center">
            <p className="text-slate-600">No questionnaires yet.</p>
            <p className="text-xs text-slate-400 mt-2">Click “+ Start questionnaire” to spin up a SIG Lite or CAIQ.</p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {list.map((q) => (
              <li key={q.questionnaire_id}>
                <button
                  onClick={() => setOpenId(q.questionnaire_id)}
                  className="w-full text-left p-4 hover:bg-slate-50 transition flex items-center justify-between"
                >
                  <div>
                    <div className="font-semibold">{q.name}</div>
                    <div className="text-xs text-slate-500 mt-0.5">
                      {q.answered} / {q.total} answered · {q.template_key} · updated {new Date(q.updated_at).toLocaleString()}
                    </div>
                  </div>
                  <ProgressBar pct={q.total > 0 ? Math.round((q.answered / q.total) * 100) : 0} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {openId && (
        <QuestionnaireView
          qid={openId}
          onClose={() => setOpenId(null)}
          onChange={reload}
          excelParse={excelCache.current.get(openId) ?? null}
        />
      )}
      {showNew && templates && (
        <NewQuestionnaireModal
          templates={templates}
          onClose={() => setShowNew(false)}
          onCreated={(id) => { setShowNew(false); setOpenId(id); reload(); }}
        />
      )}
      {showUpload && (
        <ExcelUploadModal
          onClose={() => setShowUpload(false)}
          onCreated={(id, parse) => {
            excelCache.current.set(id, parse);
            setShowUpload(false);
            setOpenId(id);
            reload();
          }}
        />
      )}
    </div>
  );
}

function ExcelUploadModal({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (qid: string, parse: ExcelParseResult) => void;
}) {
  const [file,    setFile]    = useState<File | null>(null);
  const [parse,   setParse]   = useState<ExcelParseResult | null>(null);
  const [busy,    setBusy]    = useState(false);
  const [err,     setErr]     = useState<string | null>(null);

  async function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setBusy(true);
    setErr(null);
    try {
      const p = await parseExcel(f);
      if (p.rows.length === 0) {
        setErr("Couldn't find anything that looks like questions in this file. The first sheet should have a column of questions.");
        setParse(null);
      } else {
        setParse(p);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function upload() {
    if (!file || !parse) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.questionnaireFromExcel({
        filename: file.name,
        name:     file.name.replace(/\.xlsx?$/i, ""),
        rows:     parse.rows.map((row) => ({ row_idx: row.row_idx, question: row.question, category: row.category })),
      });
      onCreated(r.questionnaire_id, parse);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-2xl p-6 w-full max-w-2xl shadow-xl max-h-[90vh] flex flex-col">
        <h2 className="text-xl font-bold">Upload questionnaire (.xlsx)</h2>
        <p className="text-sm text-slate-600 mt-1">
          We'll auto-detect the question column, create a questionnaire, and
          you can ✨-suggest answers grounded on your live findings + policies.
          On export, the answers go back into the original Excel layout.
        </p>

        {!parse && (
          <div className="mt-4">
            <label className={`block rounded-lg border-2 border-dashed p-8 text-center cursor-pointer transition ${
              busy ? "border-slate-200 bg-slate-50" : "border-slate-300 hover:border-blue-400 hover:bg-blue-50/30"
            }`}>
              <input
                type="file"
                accept=".xlsx,.xls"
                onChange={onFileChange}
                disabled={busy}
                className="hidden"
              />
              <div className="text-sm text-slate-600">
                {busy ? "Parsing…" : "Click to pick a .xlsx file"}
              </div>
              {file && !busy && (
                <div className="text-xs text-slate-400 mt-2">{file.name}</div>
              )}
            </label>
          </div>
        )}

        {parse && (
          <div className="mt-4 flex-1 overflow-y-auto">
            <div className="text-sm space-y-1">
              <div><span className="text-slate-500">File:</span> <span className="font-mono">{file?.name}</span></div>
              <div><span className="text-slate-500">Sheet:</span> <span className="font-mono">{parse.sheetName}</span></div>
              <div>
                <span className="text-slate-500">Question column:</span>{" "}
                <span className="font-mono">{colLetter(parse.questionCol)}</span>
                {parse.categoryCol !== null && (
                  <> · <span className="text-slate-500">category:</span> <span className="font-mono">{colLetter(parse.categoryCol)}</span></>
                )}
              </div>
              <div className="text-green-700 font-medium">
                {parse.rows.length} questions detected
              </div>
            </div>

            <div className="mt-4 max-h-64 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-100">
                  <tr>
                    <th className="text-left py-2 px-3 w-12 text-slate-500">Row</th>
                    <th className="text-left py-2 px-3 text-slate-500">Category</th>
                    <th className="text-left py-2 px-3 text-slate-500">Question</th>
                  </tr>
                </thead>
                <tbody>
                  {parse.rows.slice(0, 50).map((r) => (
                    <tr key={r.row_idx} className="border-t border-slate-200">
                      <td className="py-1.5 px-3 font-mono text-slate-400">{r.row_idx + 1}</td>
                      <td className="py-1.5 px-3 text-slate-600">{r.category ?? ""}</td>
                      <td className="py-1.5 px-3">{r.question}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {parse.rows.length > 50 && (
                <div className="text-xs text-slate-400 p-2 text-center">…and {parse.rows.length - 50} more</div>
              )}
            </div>
          </div>
        )}

        {err && <div className="mt-3 p-3 rounded-lg bg-red-50 text-red-700 text-sm">{err}</div>}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} disabled={busy} className="px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-sm">
            Cancel
          </button>
          {parse && (
            <button onClick={upload} disabled={busy} className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white text-sm font-medium">
              {busy ? "Uploading…" : `Create with ${parse.rows.length} questions`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="w-32 h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className="h-full bg-blue-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-500 w-10 text-right">{pct}%</span>
    </div>
  );
}

function NewQuestionnaireModal({ templates, onClose, onCreated }: {
  templates: { key: string; name: string; question_count: number }[];
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [key,  setKey]  = useState(templates[0]?.key ?? "");
  const [busy, setBusy] = useState(false);
  const [err,  setErr]  = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.createQuestionnaire({ template_key: key });
      onCreated(r.questionnaire_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <form onSubmit={submit} className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
        <h2 className="text-xl font-bold">Start a questionnaire</h2>
        <p className="text-sm text-slate-600 mt-1">
          Auto-fills from your current findings. You can override any answer afterwards.
        </p>
        <div className="mt-4 space-y-3 text-sm">
          {templates.map((t) => (
            <label key={t.key} className={`block p-3 rounded-lg border cursor-pointer transition ${
              key === t.key ? "border-blue-500 bg-blue-50" : "border-slate-200 hover:bg-slate-50"
            }`}>
              <input
                type="radio"
                name="template"
                checked={key === t.key}
                onChange={() => setKey(t.key)}
                className="mr-2"
              />
              <span className="font-medium">{t.name}</span>
              <span className="text-xs text-slate-500 ml-2">({t.question_count} questions)</span>
            </label>
          ))}
        </div>
        {err && <p className="mt-3 text-red-600 text-xs">{err}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-sm">
            Cancel
          </button>
          <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white text-sm font-medium">
            {busy ? "Auto-filling…" : "Start"}
          </button>
        </div>
      </form>
    </div>
  );
}

function QuestionnaireView({ qid, onClose, onChange, excelParse }: {
  qid: string;
  onClose: () => void;
  onChange: () => void;
  excelParse: ExcelParseResult | null;
}) {
  const [q,         setQ]         = useState<QuestionnaireDetail | null>(null);
  const [err,       setErr]       = useState<string | null>(null);
  const [suggesting, setSuggesting] = useState<string | null>(null);
  const [suggestingAll, setSuggestingAll] = useState(false);

  async function reload() {
    try {
      const x = await api.getQuestionnaire(qid);
      setQ(x);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [qid]);

  async function patchAnswer(item: QuestionnaireItem, answer: string | null) {
    try {
      await api.patchQuestionnaireItem(qid, item.item_id, { answer });
      await reload();
      onChange();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function suggest(item: QuestionnaireItem) {
    setSuggesting(item.item_id);
    setErr(null);
    try {
      await api.suggestQuestionnaireItem(qid, item.item_id);
      await reload();
      onChange();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSuggesting(null);
    }
  }

  async function suggestAllUnanswered() {
    if (!q) return;
    const unanswered = q.items.filter((it) => !it.answer || it.confidence === "manual");
    if (unanswered.length === 0) return;
    setSuggestingAll(true);
    setErr(null);
    // Limit concurrency to avoid overwhelming Anthropic / Lambda
    const CONCURRENCY = 4;
    const queue = [...unanswered];
    async function worker() {
      while (queue.length > 0) {
        const it = queue.shift();
        if (!it) break;
        try { await api.suggestQuestionnaireItem(qid, it.item_id); } catch { /* keep going */ }
      }
    }
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
    setSuggestingAll(false);
    await reload();
    onChange();
  }

  function exportToExcel() {
    if (!q || !excelParse) return;
    const answers = q.items
      .filter((it) => it.source_row_idx !== null)
      .map((it) => ({
        row_idx: it.source_row_idx as number,
        answer:  it.answer ?? "",
        notes:   it.notes ?? "",
      }));
    const outName = (q.source_filename ?? `${q.name}.xlsx`).replace(/\.xlsx?$/i, "") + "-filled.xlsx";
    writeBackAndDownload(excelParse, answers, outName);
  }

  const byCategory = useMemo(() => {
    const m = new Map<string, QuestionnaireItem[]>();
    (q?.items ?? []).forEach((it) => {
      const cat = it.category ?? "Other";
      if (!m.has(cat)) m.set(cat, []);
      m.get(cat)!.push(it);
    });
    return Array.from(m.entries());
  }, [q]);

  return (
    <div className="fixed inset-0 bg-black/40 flex items-stretch justify-center p-4 z-50">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-4xl flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-slate-200 flex-wrap gap-2">
          <h2 className="font-semibold">{q?.name ?? "Loading…"}</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={suggestAllUnanswered}
              disabled={!q || suggestingAll}
              title="Ask Claude to draft answers for every unanswered or manual item"
              className="text-xs px-3 py-1.5 rounded-md bg-purple-600 hover:bg-purple-700 disabled:bg-slate-300 text-white font-medium transition"
            >
              {suggestingAll ? "Suggesting…" : "✨ Suggest all"}
            </button>
            {excelParse && (
              <button
                onClick={exportToExcel}
                disabled={!q}
                title="Write answers back into the original Excel layout and download"
                className="text-xs px-3 py-1.5 rounded-md bg-green-600 hover:bg-green-700 disabled:bg-slate-300 text-white font-medium transition"
              >
                ⬇️ Export filled .xlsx
              </button>
            )}
            <button onClick={onClose} className="text-slate-400 hover:text-slate-700">✕</button>
          </div>
        </div>

        {err && <div className="mx-4 mt-3 p-3 rounded-lg bg-red-50 text-red-700 text-sm">{err}</div>}

        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {q === null ? (
            <p className="text-slate-500 text-sm">Loading…</p>
          ) : (
            byCategory.map(([cat, items]) => (
              <div key={cat}>
                <h3 className="text-xs uppercase tracking-wide text-slate-500 font-semibold">{cat}</h3>
                <ul className="mt-2 space-y-2">
                  {items.map((it) => (
                    <li key={it.item_id} className="rounded-lg border border-slate-200 p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1">
                          <div className="text-xs font-mono text-slate-400">{it.question_id}</div>
                          <div className="text-sm font-medium mt-0.5">{it.question}</div>
                          {it.evidence?.check_ids && it.evidence.check_ids.length > 0 && (
                            <div className="text-xs text-slate-500 mt-1">
                              <span className="font-medium">Evidence:</span>{" "}
                              {it.evidence.check_ids.join(", ")}{" "}
                              {typeof it.evidence.pass === "number" && (
                                <span className="text-slate-400">· {it.evidence.pass} pass / {it.evidence.fail ?? 0} fail</span>
                              )}
                            </div>
                          )}
                        </div>
                        <div className="flex flex-col items-end gap-1">
                          <div className="flex items-center gap-1.5">
                            <button
                              type="button"
                              onClick={() => suggest(it)}
                              disabled={suggesting === it.item_id}
                              title="Ask Claude to draft an answer grounded on your findings + policies"
                              className="text-xs px-2 py-1 rounded-md bg-purple-100 hover:bg-purple-200 disabled:bg-slate-100 text-purple-700 font-medium transition"
                            >
                              {suggesting === it.item_id ? "…" : "✨"}
                            </button>
                            <select
                              value={it.answer ?? ""}
                              onChange={(e) => patchAnswer(it, e.target.value || null)}
                              className="text-sm px-2 py-1 rounded-md border border-slate-300 bg-white"
                            >
                              <option value="">— pick —</option>
                              <option value="yes">Yes</option>
                              <option value="no">No</option>
                              <option value="partial">Partial</option>
                              <option value="n/a">N/A</option>
                            </select>
                          </div>
                          {it.confidence && (
                            <span className={`text-xs ${
                              it.confidence === "manual"        ? "text-blue-600" :
                              it.confidence === "ai-suggested"  ? "text-purple-600" :
                              "text-slate-400"
                            }`}>
                              {it.confidence}
                            </span>
                          )}
                          {it.notes && it.confidence === "ai-suggested" && (
                            <div className="text-xs text-slate-500 italic max-w-[220px] text-right">
                              {it.notes}
                            </div>
                          )}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
