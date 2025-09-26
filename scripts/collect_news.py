# scripts/collect_news.py
import os, json, datetime, time, re
from typing import List, Dict, Any
import requests
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

# ==== 환경변수 ====
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
TARGET_DATE = os.environ.get("TARGET_DATE", "").strip()  # YYYY-MM-DD, 미지정시 오늘(KST)

# ==== 날짜/경로 ====
def today_kst_str():
    KST = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(KST).date().isoformat()

CENTER_DAY = TARGET_DATE if TARGET_DATE else today_kst_str()   # 요약 기준일 (당일·전일·전전일)
OUTDIR = os.path.join("docs", "data")
os.makedirs(OUTDIR, exist_ok=True)
OUTFILE = os.path.join(OUTDIR, f"{CENTER_DAY}.json")

# ==== 카테고리 규칙 ====
CAT_RULES = {
    "철강경제": ["HRC","후판","철근","철광석","원료탄","철강","스프레드","관세","232","AD","CVD","전력요금","탄소"],
    "포스코그룹": ["포스코","POSCO","포항","광양","포스코인터내셔널","포스코퓨처엠","포스코이앤씨","CAPEX","조업","안전","노사","공급망"],
    "정비 로봇·AI정비": ["정비","보수","PdM","CBM","예지보전","로봇","드론","비전","디지털 트윈","GenAI","Agent","MRO"],
}

ORDER = ["철강경제","포스코그룹","정비 로봇·AI정비","보충"]

# ==== RSS 소스 (네이버 불필요) ====
RSS_SOURCES = [
    # 기본지
    "https://www.mk.co.kr/rss/30100041/",                      # 매경 산업
    "https://www.hankyung.com/feed/economy",                   # 한경 경제
    "https://biz.chosun.com/rss",                              # 조선비즈
    "https://www.joongang.co.kr/section/economy/rss",          # 중앙 경제
    "https://www.donga.com/news/rss/list/2/",                  # 동아 경제
    # 보조(정비/로봇/AI/MRO)
    "https://www.etnews.com/rss/news.xml",                     # 전자신문
    "http://www.robotnews.net/rss/allArticle.xml",             # 로봇신문
]

# ==== 유틸 ====
UA = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

def http_get(url: str, timeout=12):
    return requests.get(url, timeout=timeout, headers=UA, allow_redirects=True)

def strip_html(s: str) -> str:
    return re.sub("<.*?>", "", s or "").strip()

def hostname(url: str) -> str:
    try:
        return requests.utils.urlparse(url).netloc.replace("www.","")
    except Exception:
        return "출처"

def parse_rss(url: str) -> List[Dict[str, Any]]:
    try:
        r = http_get(url, timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.content, "xml")
        out = []
        for it in soup.find_all("item"):
            t = strip_html(it.title.text if it.title else "")
            link = (it.link.text if it.link else "") or ""
            pub = (it.pubDate.text if it.pubDate else "") or ""
            out.append({"title": t, "url": link, "src": hostname(link) or hostname(url), "ts": pub})
        return out
    except Exception:
        return []

def parse_pub_ts(ts: str):
    # 다양한 포맷 대비 (날짜만 쓰면 됨)
    for fmt in ("%a, %d %b %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(ts[:len(fmt)], fmt)
        except Exception:
            continue
    return None

def in_last_3days(center_day: str, ts: str) -> bool:
    target = datetime.datetime.strptime(center_day, "%Y-%m-%d")
    start = target - datetime.timedelta(days=2)  # 전전일 00:00
    end   = target + datetime.timedelta(days=1)  # +1일 00:00 미만
    dt = parse_pub_ts(ts)
    if not dt:
        # 날짜 파싱 안 되면 일단 포함 (RSS 소스가 가끔 비표준)
        return True
    return start <= dt < end

def is_alive_and_title_match(url: str, rss_title: str, thr=0.35) -> bool:
    try:
        r = http_get(url, timeout=10)
        if r.status_code != 200:
            return False
        html = BeautifulSoup(r.text, "html.parser")
        page_title = (html.title.text if html.title else "").strip()
        if not page_title:
            return False
        sim = SequenceMatcher(None, strip_html(rss_title), strip_html(page_title)).ratio()
        return sim >= thr
    except Exception:
        return False

def dedup_keep_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen=set(); out=[]
    for it in items:
        k=(it.get("title","").strip(), it.get("url","").strip())
        if k in seen: continue
        seen.add(k); out.append(it)
    return out

def categorize(title: str) -> str:
    t = title.upper()
    score = {k:0 for k in CAT_RULES}
    for cat, kws in CAT_RULES.items():
        for kw in kws:
            if kw.upper() in t: score[cat]+=1
    best = sorted(score.items(), key=lambda x:(-x[1], ORDER.index(x[0])))
    return best[0][0] if best[0][1]>0 else "보충"

# ==== 후보 수집 (RSS만으로도 동작) ====
def collect_candidates(center_day: str) -> Dict[str, List[Dict[str, Any]]]:
    bucket=[]
    for u in RSS_SOURCES:
        bucket += parse_rss(u)
        time.sleep(0.1)
    # 3일 필터 → 중복 제거
    bucket = [it for it in bucket if in_last_3days(center_day, it.get("ts",""))]
    bucket = dedup_keep_order(bucket)

    # 링크 유효성 + 제목 매칭 검사
    alive=[]
    for it in bucket:
        url = it.get("url","")
        title = it.get("title","")
        if not url or not title: continue
        if is_alive_and_title_match(url, title):
            alive.append(it)
        if len(alive) >= 80:  # 과도 방지
            break

    # 카테고리 분류 + 상한
    by_cat = {k:[] for k in ORDER}
    for it in alive:
        cat = categorize(it["title"])
        by_cat.setdefault(cat, []).append(it)

    # 상한: 철강경제3 / 포스코3 / 정비2~3 / 보충2
    cap = {"철강경제":3, "포스코그룹":3, "정비 로봇·AI정비":3, "보충":2}
    for k in by_cat:
        by_cat[k] = by_cat[k][:cap.get(k,3)]
    return by_cat

# ==== 프롬프트 구성 (사용자 제공 사양 그대로) ====
def build_prompt(center_day: str, provided: Dict[str, List[Dict[str, Any]]]) -> str:
    base = """
Search for Korean business/industry news and summarize **from a steel maintenance company’s POV**.

[수집 범위]
- 기본: 매일경제, 한국경제, 조선·중앙·동아일보 ‘경제면’(온라인 포함)
- 보조(정비/로봇/AI/MRO 한정): 전자신문, 로봇신문, 산업/부처 보도자료, 조선·항공·철도 전문지, 지역지(포항·경북)
- 기간: **당일·전일·전전일(최근 3일)** 기사만. 전일 우선, 중복 최소화.
- 모든 항목은 제목 줄에 **[원문] 링크** 포함, **링크 유효성(HTTP 200 & 제목 일치)** 확인. 오링크/유실 시 **대체 기사로 교체**.

[카테고리 – 철강정비 관점으로 재편]
1) **철강경제** 3건: 글로벌/국내 **강재·원료 가격(HRC·후판·철근·철광석·원료탄)**, **스프레드**, **무역·관세(232·AD/CVD)**, **정책**(탄소·전력요금 등). **수치·지표·정책명** 명시.
2) **포스코그룹** 3건: POSCO홀딩스/포스코/포스코인터내셔널/포스코퓨처엠/포스코이앤씨 등 **실적·CAPEX·조업/안전·노사·공급망**. **포항/광양** 현장 이슈 우선. **경영기획 시사점** 포함.
3) **정비 로봇·AI정비** 3건(부족시 1~2건): 제철소 **설비정비·정기보수·PdM/CBM·예지보전**, **정비 로봇/드론·비전검사·디지털트윈·GenAI/Agent** 등 **도입·실증·ROI** 사례. 철도/조선/항공 **MRO 벤치마킹** 허용.
- *부족한 날은 해당 건수만 표기하고 억지로 채우지 말 것(스팸 방지).* 필요 시 보충 카테고리(**철강산업 일반**, **제조·사무 AI 혁신**)로 1~2건 보완하되, **철강정비 연관성**을 명확히 설명.

[출력 형식(JSON) — 반드시 아래 스키마만!]
JSON array only. 각 원소 스키마:
{
  "category": "철강경제|포스코그룹|정비 로봇·AI정비|보충",
  "title": "제목",
  "src": "언론사/기관",
  "url": "원문 링크(HTTP 200)",
  "ts": "YYYY-MM-DD",
  "points": ["- 핵심 요약 1", "- 핵심 요약 2(수치·정책명은 **굵게**)"],
  "insight": "☞ 시사점 한 줄"
}

[중요]
- 아래 "Provided Articles" 목록 **내 기사만 사용**. 목록에 없는 링크·사실은 쓰지 말 것.
- 카테고리별 **최대 3건**(정비는 2~3건). 부족하면 있는 만큼만.
- 중복·유사 제목 제거, **수치/기관/정책명은 굵게** 표시.
"""
    lines = [f"\nProvided Articles (기준일 {center_day}, 최근 3일):\n"]
    for cat in ORDER:
        items = provided.get(cat, [])
        if not items: continue
        lines.append(f"## {cat}")
        for it in items:
            lines.append(f"- title: {it.get('title')}\n  src: {it.get('src')}\n  url: {it.get('url')}\n  ts: {center_day}")
    tail = "\nOutput JSON only. No prose, no markdown."
    return base + "\n".join(lines) + tail

# ==== OpenAI 호출 ====
def ask_openai(prompt: str) -> List[Dict[str, Any]]:
    if not OPENAI_API_KEY:
        return []
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        rsp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            messages=[
                {"role":"system","content":"You are a rigorous news summarizer for a steel maintenance company. Output strictly valid JSON."},
                {"role":"user","content":prompt}
            ],
            max_tokens=1400,
        )
        content = rsp.choices[0].message.content.strip()
        m = re.search(r"```json(.*?)```", content, re.S)
        if m: content = m.group(1).strip()
        data = json.loads(content)
        return data if isinstance(data, list) else []
    except Exception as e:
        print("OpenAI error:", e)
        return []

# ==== 메인 ====
def main():
    # 1) 후보 수집(RSS) → 3일 필터 → HTTP 200 & 제목 유사도 확인
    provided = collect_candidates(CENTER_DAY)

    # 2) 프롬프트 구성(요청하신 포맷 그대로)
    prompt = build_prompt(CENTER_DAY, provided)

    # 3) OpenAI 요약 (실패/쿼터시 빈 리스트 반환)
    data = ask_openai(prompt)

    # 4) 최종 저장 (데모/임의 데이터 없음)
    with open(OUTFILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Wrote {OUTFILE} ({len(data)} items)")

if __name__ == "__main__":
    main()
