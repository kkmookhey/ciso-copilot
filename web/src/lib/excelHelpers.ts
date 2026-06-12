/**
 * Client-side Excel parsing + write-back for vendor security questionnaires.
 *
 * Parsing strategy:
 *  - Score every sheet by how many cells look like questions; pick the
 *    highest-scoring one (handles multi-sheet workbooks where the real
 *    questions live on a sheet other than the first — e.g. an
 *    "Instructions" or "Cover" tab sits at index 0).
 *  - Auto-detect a "question" column: the column with the most cells whose
 *    text looks like a question (>= 20 chars, ends with "?", or contains
 *    typical question vocabulary)
 *  - Auto-detect a category column: a left-of-question column with short
 *    repeating-ish values
 *  - Caller can override the detected columns
 *
 * Write-back strategy:
 *  - Preserve the original workbook bytes (we kept it via FileReader)
 *  - Write each answer into the same row at the chosen answer column on
 *    the detected sheet
 *  - Trigger download of the modified .xlsx
 */
import * as XLSX from "xlsx";

export interface DetectedRow {
  row_idx:  number;
  question: string;
  category?: string;
}

export interface ExcelParseResult {
  workbook:      XLSX.WorkBook;
  sheetName:     string;
  headerRowIdx:  number | null;
  questionCol:   number;
  categoryCol:   number | null;
  rows:          DetectedRow[];
  totalRows:     number;
}

const QUESTION_HINTS = [
  /\?$/,
  /^(is|are|does|do|can|will|has|have|should|did)\b/i,
  /\b(implement|enforce|encrypt|comply|conform|maintain|provide|review)\b/i,
];

function looksLikeQuestion(s: string): boolean {
  const t = (s ?? "").trim();
  if (t.length < 12) return false;
  return QUESTION_HINTS.some((re) => re.test(t));
}

function scoreSheetForQuestions(aoa: string[][]): number {
  let score = 0;
  for (const row of aoa) {
    for (const cell of row) {
      if (looksLikeQuestion(String(cell ?? ""))) score++;
    }
  }
  return score;
}

export async function parseExcel(file: File): Promise<ExcelParseResult> {
  const buf = await file.arrayBuffer();
  const workbook = XLSX.read(buf, { type: "array" });
  if (workbook.SheetNames.length === 0) throw new Error("No worksheet found");

  // Pick the sheet with the most question-looking cells. Falls back to the
  // first sheet if no sheet scores any questions (preserves prior behavior
  // for non-questionnaire files).
  let sheetName = workbook.SheetNames[0];
  let aoa: string[][] = [];
  let bestSheetScore = -1;
  for (const name of workbook.SheetNames) {
    const ws = workbook.Sheets[name];
    if (!ws) continue;
    const sheetAoa = XLSX.utils.sheet_to_json<string[]>(ws, { header: 1, defval: "", raw: false }) as string[][];
    const sheetScore = scoreSheetForQuestions(sheetAoa);
    if (sheetScore > bestSheetScore) {
      bestSheetScore = sheetScore;
      sheetName = name;
      aoa = sheetAoa;
    }
  }
  const sheet = workbook.Sheets[sheetName];
  if (!sheet) throw new Error("No worksheet found");

  // Detect the column with the highest count of cells that look like questions.
  const colScores: number[] = [];
  const colNonEmpty: number[] = [];
  for (const row of aoa) {
    for (let c = 0; c < row.length; c++) {
      const v = String(row[c] ?? "");
      if (v.trim().length > 0) {
        colNonEmpty[c] = (colNonEmpty[c] ?? 0) + 1;
      }
      if (looksLikeQuestion(v)) {
        colScores[c] = (colScores[c] ?? 0) + 1;
      }
    }
  }
  let questionCol = 0;
  let bestScore = -1;
  colScores.forEach((s, c) => {
    if (s > bestScore) { bestScore = s; questionCol = c; }
  });

  // Detect a category column — a column to the left of questionCol with
  // many non-empty short cells (typical SIG/CAIQ layout has Category | Question).
  let categoryCol: number | null = null;
  for (let c = questionCol - 1; c >= 0; c--) {
    const filled = colNonEmpty[c] ?? 0;
    if (filled >= Math.min(10, Math.floor((bestScore || 0) * 0.5))) {
      categoryCol = c;
      break;
    }
  }

  // Find the header row (the row above the first question-looking row in
  // questionCol) so we can write answers below it.
  let headerRowIdx: number | null = null;
  let firstQuestionRow = -1;
  for (let r = 0; r < aoa.length; r++) {
    if (looksLikeQuestion(String(aoa[r]?.[questionCol] ?? ""))) {
      firstQuestionRow = r;
      headerRowIdx = r === 0 ? null : r - 1;
      break;
    }
  }

  const rows: DetectedRow[] = [];
  if (firstQuestionRow >= 0) {
    for (let r = firstQuestionRow; r < aoa.length; r++) {
      const q = String(aoa[r]?.[questionCol] ?? "").trim();
      if (!q || !looksLikeQuestion(q)) continue;
      const cat = categoryCol !== null ? String(aoa[r]?.[categoryCol] ?? "").trim() : undefined;
      rows.push({ row_idx: r, question: q, category: cat || undefined });
    }
  }

  return { workbook, sheetName, headerRowIdx, questionCol, categoryCol, rows, totalRows: aoa.length };
}

/// Writes answers back into the workbook at (row_idx, answerCol) and triggers
/// a download. answerCol defaults to questionCol + 1.
export function writeBackAndDownload(
  parse: ExcelParseResult,
  answers: Array<{ row_idx: number; answer: string; notes?: string }>,
  filename: string,
  answerCol?: number,
): void {
  const ws = parse.workbook.Sheets[parse.sheetName];
  if (!ws) throw new Error("Sheet missing on write-back");

  const ansCol  = answerCol ?? (parse.questionCol + 1);
  const notesCol = ansCol + 1;

  for (const a of answers) {
    const cellRef       = XLSX.utils.encode_cell({ r: a.row_idx, c: ansCol });
    const notesCellRef  = XLSX.utils.encode_cell({ r: a.row_idx, c: notesCol });
    ws[cellRef]      = { t: "s", v: a.answer ?? "" };
    if (a.notes !== undefined) {
      ws[notesCellRef] = { t: "s", v: a.notes ?? "" };
    }
  }

  // Update sheet range to ensure new columns are included.
  const range = XLSX.utils.decode_range(ws["!ref"] || "A1");
  range.e.c = Math.max(range.e.c, notesCol);
  ws["!ref"] = XLSX.utils.encode_range(range);

  const blob = XLSX.write(parse.workbook, { type: "array", bookType: "xlsx" });
  const url = URL.createObjectURL(new Blob([blob], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/// Column-letter helper (0 -> "A", 1 -> "B", 26 -> "AA").
export function colLetter(idx: number): string {
  let s = "";
  let n = idx;
  while (n >= 0) {
    s = String.fromCharCode((n % 26) + 65) + s;
    n = Math.floor(n / 26) - 1;
  }
  return s;
}
