# main.py
import base64, io, json, os, re, time
from typing import Optional, Union

import httpx
import pandas as pd
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, HttpUrl, ValidationError
from playwright.async_api import async_playwright

# Optional PDF extractor (Windows-friendly)
try:
    import pdfplumber
except Exception:
    pdfplumber = None

app = FastAPI(title="LLM Analysis Quiz Solver", version="1.0.0")

EXPECTED_SECRET = os.getenv("QUIZ_SECRET", "CHANGE_ME")
QUIZ_TOTAL_SECONDS = int(os.getenv("QUIZ_TOTAL_SECONDS", "170"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "40"))
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (QuizSolver)")

class QuizPOST(BaseModel):
    email: str
    secret: str
    url: HttpUrl

def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

async def fetch_rendered_html(url: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle", timeout=HTTP_TIMEOUT * 1000)
        await page.wait_for_timeout(500)
        html = await page.content()
        await context.close()
        await browser.close()
        return html

BASE64_INNER_HTML_RE = re.compile(r"atob\(`([^`]+)`\)", re.IGNORECASE)
def try_decode_base64_blocks(html: str) -> str:
    def _rep(m: re.Match) -> str:
        payload = m.group(1).replace("\n", "")
        try:
            return base64.b64decode(payload).decode("utf-8", errors="ignore")
        except Exception:
            return m.group(0)
    return BASE64_INNER_HTML_RE.sub(_rep, html)

SUBMIT_URL_RE = re.compile(r"(?:submit\s+to|POST\s+to)\s+(https?://[^\s\"'<>]+)", re.IGNORECASE)
def extract_submit_url(text: str) -> Optional[str]:
    m = SUBMIT_URL_RE.search(text)
    return m.group(1) if m else None

JSON_BLOCK_RE = re.compile(r"(?s)<pre>\s*({.*?})\s*</pre>")
def find_embedded_json_payload(text: str) -> Optional[dict]:
    m = JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None

async def http_get_bytes(url: str, client: Optional[httpx.AsyncClient] = None) -> bytes:
    own = False
    if client is None:
        own = True
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        r = await client.get(url)
        r.raise_for_status()
        return r.content
    finally:
        if own:
            await client.aclose()

def sum_value_column_pdf_pdfplumber(pdf_bytes: bytes, column_name: str, page_number_one_based: int) -> Optional[float]:
    if pdfplumber is None:
        return None
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_idx = page_number_one_based - 1
            if page_idx < 0 or page_idx >= len(pdf.pages):
                return None
            page = pdf.pages[page_idx]
            tables = page.extract_tables() or []
            for tbl in tables:
                headers = [normalize_ws(h or "").lower() for h in (tbl[0] or [])]
                if column_name.lower() in headers:
                    idx = headers.index(column_name.lower())
                    vals = []
                    for row in tbl[1:]:
                        if idx < len(row):
                            cell = (row[idx] or "").replace(",", "").strip()
                            m = re.search(r"-?\d+(?:\.\d+)?", cell)
                            if m:
                                vals.append(float(m.group(0)))
                    s = float(sum(vals))
                    return int(s) if abs(s - int(s)) < 1e-9 else s
            # fallback rough text parse
            text = page.extract_text() or ""
            total = 0.0
            found = False
            for line in text.splitlines():
                if column_name.lower() in line.lower():
                    for n in re.findall(r"-?\d+(?:\.\d+)?", line.replace(",", "")):
                        total += float(n); found = True
            if found:
                return int(total) if abs(total - int(total)) < 1e-9 else total
    except Exception:
        return None
    return None

async def solve_quiz_chain(email: str, secret: str, first_url: str, deadline_ts: float):
    client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    steps = 0
    current_url = first_url
    last_submit_status = None

    try:
        while True:
            if time.time() >= deadline_ts:
                raise TimeoutError("Time budget exceeded.")
            html = await fetch_rendered_html(current_url)
            expanded = try_decode_base64_blocks(html)
            text = normalize_ws(re.sub(r"<[^>]+>", " ", expanded))

            submit_url = extract_submit_url(text)
            if not submit_url:
                payload_hint = find_embedded_json_payload(expanded) or {}
                submit_url = payload_hint.get("submit")
            if not submit_url:
                raise RuntimeError("Submit URL not found on the quiz page.")

            answer: Union[str, int, float, bool, dict, None] = None

            # Heuristic: PDF "sum of the 'value' column on page N"
            m = re.search(
                r"sum of the ['\"]?([A-Za-z0-9_ -]+)['\"]?\s+column\s+in\s+the\s+table\s+on\s+page\s+(\d+)",
                text, flags=re.IGNORECASE
            )
            if m:
                col = m.group(1).strip()
                page_no = int(m.group(2))
                pdf_links = re.findall(r"(https?://[^\s\"'<>]+\.pdf)", expanded, flags=re.IGNORECASE)
                if not pdf_links:
                    raise RuntimeError("No PDF link found for the task.")
                pdf_bytes = await http_get_bytes(pdf_links[0], client=client)
                s = sum_value_column_pdf_pdfplumber(pdf_bytes, column_name=col, page_number_one_based=page_no)
                if s is None:
                    raise RuntimeError("Failed to compute sum from PDF.")
                answer = s

            # Heuristic: “how many rows” in HTML
            if answer is None and re.search(r"how many rows", text, re.IGNORECASE):
                rows = len(re.findall(r"<tr\b", html, re.IGNORECASE))
                answer = rows

            # Heuristic: CSV/JSON/XLSX “value” column sum
            if answer is None:
                data_links = re.findall(r"(https?://[^\s\"'<>]+\.(?:csv|json|xlsx?))", expanded, flags=re.IGNORECASE)
                if data_links:
                    data_url = data_links[0]
                    data_bytes = await http_get_bytes(data_url, client=client)
                    if data_url.lower().endswith(".csv"):
                        df = pd.read_csv(io.BytesIO(data_bytes))
                    elif data_url.lower().endswith(".json"):
                        obj = json.loads(data_bytes.decode("utf-8", errors="ignore"))
                        df = pd.json_normalize(obj)
                    else:
                        df = pd.read_excel(io.BytesIO(data_bytes))
                    cols_lower = {c.lower(): c for c in df.columns}
                    if "value" in cols_lower:
                        colname = cols_lower["value"]
                        s = float(pd.to_numeric(df[colname], errors="coerce").fillna(0).sum())
                        answer = int(s) if abs(s - int(s)) < 1e-9 else float(s)

            if answer is None:
                raise RuntimeError("No solver matched. Extend heuristics.")

            payload = {"email": email, "secret": secret, "url": current_url, "answer": answer}
            try:
                resp = await client.post(submit_url, json=payload)
                last_submit_status = resp.status_code
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                raise RuntimeError(f"Submit failed: {e}") from e

            steps += 1
            next_url = data.get("url") if isinstance(data, dict) else None
            if next_url:
                current_url = next_url
                continue
            return {
                "ok": True,
                "steps": steps,
                "last_url": current_url,
                "last_submit_status": last_submit_status
            }
    finally:
        await client.aclose()

@app.get("/")
def root():
    return {"ok": True, "msg": "Server is up"}

@app.post("/quiz")
async def quiz_endpoint(req: Request):
    # Validate JSON
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    # Validate schema
    try:
        payload = QuizPOST(**data)
    except ValidationError as ve:
        raise HTTPException(status_code=400, detail=f"Bad payload: {ve}")
    # Secret check
    if payload.secret != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    started = time.time()
    deadline = started + QUIZ_TOTAL_SECONDS
    try:
        result = await solve_quiz_chain(payload.email, payload.secret, str(payload.url), deadline)
        result.update({"started_at": started, "finished_at": time.time()})
        return result
    except TimeoutError as te:
        raise HTTPException(status_code=408, detail=str(te))
    except Exception as e:
        return {"started_at": started, "finished_at": time.time(), "ok": False, "error": str(e)}






# main.py (minimal test)
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

@app.get("/")
def root():
    return {"ok": True, "msg": "Server is up"}

@app.post("/quiz")
async def quiz_endpoint(req: Request):
    data = await req.json()
    if "secret" not in data:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    # echo back to prove it's wired
    return {"ok": True, "echo": data}
