# scripts/collect_news.py
import os, json, datetime, time, re
from typing import List, Dict, Any
import requests
from bs4 import BeautifulSoup

# ====== 환경설정 ======
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
NAVER_ID = os.environ.get("NAVER_CLIENT_ID")
NAVER_SECRET = os.environ.get("NAVER_CLIENT_SECRET")
DEMO_MODE = os.environ.get("DEMO_MODE", "0") == "1"  # DEMO용 샘플 데이터 생성

TODAY = datetime.date.today().isoformat()
OUTDIR = os.path.join("docs", "data")
os.makedirs(OUTDIR, exist_ok=True)
OUTFILE = os.path.join(OUTDIR, f"{TODAY}.json")

CATEGORIES = {
    "철강경제": [
        "HRC 가격", "후판 가격", "철근 가격", "철광석 가격", "원료탄",
        "철강 관세 232", "철강 AD CVD", "탄소 정책 전력요금 철강"
    ],
    "포스코그룹": [
        "포스코 포항", "포스코 광양", "POSCO 홀딩스", "포스코인터내셔널",
        "포스코퓨처엠", "포스코이앤씨", "포스코 안전 조업", "포스코 CAPEX"
    ],
    "정비 로봇·AI정비": [
        "예지보전 PdM CBM", "설비 정비 로봇", "드론 검사 제철소",
        "비전 검사 제철소", "디지털 트윈 제철소", "GenAI 정비"
    ],
}

# ====== 유틸 ======
def naver_search(query: str, display: int = 10) -> List[Dict[str, Any]]:
    if not (NAVER_ID and NAVER_SECRET):
        return []
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET}
    params = {"query": query, "display": display, "sort": "date"}
    r = requests.get(url, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    items = r.json().get("items", [])
    out = []
    for it in items:
        link = it.get("originallink") or it.get("link")
        title = re.sub("<.*?>", "", it.get("title", "")).strip()
        src = it.get("link","").split("/")[2] if it.get("link") else "언론사"
        pub = it.get("pubDate", "")
        out.append({"title": title, "url": link, "src": src, "ts": pub})
    return out

def is_alive(url: str) -> bool:
    try:
        r = requests.get(url, timeout=12, allow_redirects=True, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200: return False
        # 간단히 <title> 존재만 확인
        return bool(BeautifulSoup(r.text, "html.parser").title)
    except Exception:
        return False

def dedup_keep_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set(); out=[]
    for it in items:
        k = (it.get("title","").strip(), it.get("url","").strip())
        if k in seen: continue
        seen.add(k); out.append(it)
    return out

def clamp_recent(items: List[Dict[str, Any]], days: int = 3) -> List[Dict[str, Any]]:
    # 네이버 pubDate 기준 필터(대략)
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    out=[]
    for it in items:
        try:
            # 예: 'Fri, 26 Sep 2025 09:10:00 +0900'
            dt = it["ts"]
            dt = datetime.datetime.strptime(dt[:25], "%a, %d %b %Y %H:%M:%S")
        except Exception:
            # 형식 모르면 일단 통과
            out.append(it); continue
        if dt >= cutoff: out.append(it)
    return out

def collect_candidates() -> Dict[str, List[Dict[str, Any]]]:
    if DEMO_MODE:
        demo = {
            "철강경제": [
              {"title":"HRC 가격 반등, 스프레드 확대","url":"https://example.com/a","src":"매일경제","ts":TODAY},
              {"title":"철광석 약세, 원료탄 혼조","url":"https://example.com/b","src":"한국경제","ts":TODAY},
              {"title":"전력요금 변수와 후판 마진","url":"https://example.com/c","src":"조선일보","ts":TODAY},
            ],
            "포스코그룹": [
              {"title":"포항/광양 CAPEX 점검","url":"https://example.com/d","src":"중앙일보","ts":TODAY},
              {"title":"안전·조업 안정화 프로그램","url":"https://example.com/e","src":"동아일보","ts":TODAY},
              {"title":"포스코인터내셔널 공급망 업데이트","url":"https://example.com/f","src":"전자신문","ts":TODAY},
            ],
            "정비 로봇·AI정비": [
              {"title":"제철소 PdM/CBM PoC 사례","url":"https://example.com/g","src":"로봇신문","ts":TODAY},
              {"title":"드론·비전검사 도입 ROI","url":"https://example.com/h","src":"산업부 보도","ts":TODAY}
            ],
        }
        return demo

    result = {k: [] for k in CATEGORIES.keys()}
    for cat, queries in CATEGORIES.items():
        bucket=[]
        for q in queries:
            try:
                bucket += naver_search(q, display=7)
                time.sleep(0.2)
            except Exception:
                pass
        # 최근 3일 + 살아있는 링크만
        bucket = dedup_keep_order(bucket)
        bucket = clamp_recent(bucket, days=3)
        alive=[]
        for it in bucket:
            if it.get("url") and is_alive(it["url"]):
                alive.append(it)
                if len(alive) >= (3 if cat!="정비 로봇·AI정비" else 2):
                    # 카테고리별 목표치만 추림(철강경제/포스코그룹 3건, 정비 2건)
                    break
        result[cat] = alive
    return result

def make_prompt(provided: Dict[str, List[Dict[str, Any]]]) -> str:
    # 원빈님 프롬프트를 "주어진 기사 목록만 사용" 조건으로 보강
    base = r"""
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
- *부족한 날은 해당 건수만 표기하고 억지로 채우지 말 것.* 필요 시 보충 카테고리(**철강산업 일반**, **제조·사무 AI 혁신**)로 1~2건 보완하되, **철강정비 연관성**을 명확히 설명.

[출력 형식(JSON) — 반드시 아래 스키마만!]
JSON array only. 각 원소 스키마:
{
  "category": "철강경제|포스코그룹|정비 로봇·AI정비|보충",
  "title": "굵은 제목 없이 평문 제목",
  "src": "언론사/기관",
  "url": "원문 링크(HTTP 200)",
  "ts": "YYYY-MM-DD",
  "points": ["- 핵심 요약 1", "- 핵심 요약 2(수치·정책명은 **굵게**)"],
  "insight": "☞ 시사점 한 줄"
}

[중요]
- 아래 "Provided Articles" 목록 **내 기사만 사용**. 목록에 없는 링크·사실은 쓰지 말 것.
- 카테고리별 **최대 3건**(정비는 2~3건). 부족하면 있는 만큼만.
- 중복·유사 제목 제거, 수치/정책명은 **굵게** 표시.
"""
    # 제공 기사 목록 붙이기
    lines = ["\nProvided Articles:\n"]
    for cat, items in provided.items():
        lines.append(f"## {cat}")
        for it in items:
            lines.append(f"- title: {it.get('title')}\n  src: {it.get('src')}\n  url: {it.get('url')}\n  ts: {str(it.get('ts'))[:10]}")
    tail = "\nOutput JSON only. No prose, no markdown."
    return base + "\n".join(lines) + tail

def call_openai(prompt: str) -> List[Dict[str, Any]]:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    rsp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        messages=[
            {"role":"system","content":"You are a rigorous news summarizer for a steel maintenance company. Output strictly valid JSON."},
            {"role":"user","content":prompt}
        ]
    )
    content = rsp.choices[0].message.content.strip()
    # JSON 안전 파싱
    m = re.search(r"```json(.*?)```", content, re.S)
    if m: content = m.group(1).strip()
    try:
        data = json.loads(content)
        if isinstance(data, list): return data
    except Exception:
        pass
    # 실패 시 빈 배열
    return []

def main():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required")
    provided = collect_candidates()
    prompt = make_prompt(provided)
    data = call_openai(prompt)

    # 파일 저장
    with open(OUTFILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Wrote {OUTFILE} ({len(data)} items)")

if __name__ == "__main__":
    main()
