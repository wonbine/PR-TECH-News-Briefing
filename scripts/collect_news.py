# scripts/collect_news.py
import os, json, datetime, time, re
# ...
TARGET_DATE = os.environ.get("TARGET_DATE", "").strip()

def resolve_date() -> str:
    if TARGET_DATE:
        return TARGET_DATE  # YYYY-MM-DD
    return datetime.date.today().isoformat()

TODAY = resolve_date()
OUTDIR = os.path.join("docs", "data")
os.makedirs(OUTDIR, exist_ok=True)
OUTFILE = os.path.join(OUTDIR, f"{TODAY}.json")

# 중복 방지
if os.path.exists(OUTFILE):
    print(f"[SKIP] {OUTFILE} already exists")
    raise SystemExit(0)


from typing import List, Dict, Any
import requests
from bs4 import BeautifulSoup
import openai as openai_pkg  # 예외 클래스 참조용

# ====== 환경설정 ======
# 상단 import 아래에 추가/수정
TARGET_DATE = os.environ.get("TARGET_DATE", "").strip()
def resolve_date():
    if TARGET_DATE:
        return TARGET_DATE  # YYYY-MM-DD 가정
    return datetime.date.today().isoformat()

TODAY = resolve_date()
OUTDIR = os.path.join("docs", "data")
OUTFILE = os.path.join(OUTDIR, f"{TODAY}.json")

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

def clamp_recent(items, days=3):
    # 타깃 날짜 기준 창(예: TARGET_DATE 당일 포함 최근 3일)
    target = datetime.datetime.strptime(TODAY, "%Y-%m-%d")
    start = target - datetime.timedelta(days=2)  # 전전일
    end   = target + datetime.timedelta(days=1)  # 다음날 00시 미만
    out=[]
    for it in items:
        try:
            # 예: 'Fri, 26 Sep 2025 09:10:00 +0900'
            dt = datetime.datetime.strptime(it["ts"][:25], "%a, %d %b %Y %H:%M:%S")
        except Exception:
            out.append(it); continue
        if start <= dt < end:
            out.append(it)
    return out


def collect_candidates() -> Dict[str, List[Dict[str, Any]]]:
    # 기존: NAVER 키 없으면 데모 반환  ❌
    # 수정: NAVER 키 없으면 '빈 후보' 반환  ✅
    if not (NAVER_ID and NAVER_SECRET):
        return { "철강경제": [], "포스코그룹": [], "정비 로봇·AI정비": [] }

    # (키가 있으면 원래 네이버 검색 로직 수행)
    result = {k: [] for k in CATEGORIES.keys()}
    ...
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
    """
    OpenAI 호출(최대 4회 재시도, 지수백오프).
    - per-minute rate limit 같은 일시적 429면 재시도
    - 진짜 'insufficient_quota'면 바로 빈 리스트 반환 → 상위(main)에서 폴백
    """
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    for attempt in range(4):
        try:
            rsp = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.2,
                messages=[
                    {"role":"system","content":"You are a rigorous news summarizer for a steel maintenance company. Output strictly valid JSON."},
                    {"role":"user","content":prompt}
                ],
                max_tokens=1200,   # 비용/쿼터 절약
            )
            content = rsp.choices[0].message.content.strip()

            # ```json ... ``` 방지
            m = re.search(r"```json(.*?)```", content, re.S)
            if m: content = m.group(1).strip()

            data = json.loads(content)
            return data if isinstance(data, list) else []
        except openai_pkg.RateLimitError as e:
            # 쿼터 완전 소진(insufficient_quota)이면 재시도 의미 없음 → 즉시 폴백
            if "insufficient_quota" in str(e):
                return []
            # 그 외 per-minute limit 등은 지수 백오프 후 재시도
            delay = 5 * (2 ** attempt)
            time.sleep(delay)
        except Exception:
            # 일단 재시도, 마지막 시도 후 빈 리스트
            delay = 3 * (2 ** attempt)
            time.sleep(delay)

    return []


def main():
    if os.path.exists(OUTFILE):
    print(f"[SKIP] {OUTFILE} already exists")
    return

    # DEMO 모드가 아니고, 키도 없으면 실패
    if not OPENAI_API_KEY and not DEMO_MODE:
        raise RuntimeError("OPENAI_API_KEY is required (or set DEMO_MODE=1)")

    # 이미 오늘자 파일이 있으면 재생성하지 않음(중복 과금 방지)
    if os.path.exists(OUTFILE):
        print(f"Skip: {OUTFILE} already exists.")
        return

    provided = collect_candidates()      # 네이버 API 없으면 DEMO 후보 세트
    prompt = make_prompt(provided)

    data: List[Dict[str, Any]] = []
    if DEMO_MODE:
        # DEMO: 후보 기사 그대로 간단 가공
        for cat, items in provided.items():
            for it in items[: (3 if cat != "정비 로봇·AI정비" else 2)]:
                data.append({
                    "category": cat,
                    "title": it["title"],
                    "src": it["src"],
                    "url": it["url"],
                    "ts": str(it["ts"])[:10],
                    "points": ["- (데모) 핵심 요약 1", "- (데모) 핵심 요약 2"],
                    "insight": "☞ (데모) 철강정비 관점의 시사점"
                })
    else:
        # 실데이터 호출
        data = call_openai(prompt)

        # === 폴백: 쿼터 소진/429 등으로 data가 비면, "기사 원문 목록 기반의 최소 카드" 생성 ===
        if not data:
            print("OpenAI 호출 실패/쿼터 초과 → 폴백으로 최소 카드 생성")
            for cat, items in provided.items():
                for it in items[: (3 if cat != "정비 로봇·AI정비" else 2)]:
                    data.append({
                        "category": cat,
                        "title": it["title"],
                        "src": it["src"],
                        "url": it["url"],
                        "ts": str(it["ts"])[:10],
                        "points": ["- (폴백) 기사 원문 참조", "- (폴백) 세부 요약은 쿼터 복구 후 제공"],
                        "insight": "☞ (폴백) 오늘은 링크 카드만 제공됩니다."
                    })

    os.makedirs(OUTDIR, exist_ok=True)
    with open(OUTFILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Wrote {OUTFILE} ({len(data)} items)")
if __name__ == "__main__":
    if not data:
    print("Empty result → fallback to latest or candidates")
    latest = os.path.join(OUTDIR, "latest.json")
    if os.path.exists(latest):
        try:
            data = json.load(open(latest, "r", encoding="utf-8"))
        except Exception:
            pass
if not data:
    for cat, items in provided.items():
        for it in items[: (3 if cat != "정비 로봇·AI정비" else 2)]:
            data.append({
                "category": cat,
                "title": it["title"],
                "src": it["src"],
                "url": it["url"],
                "ts": str(it["ts"])[:10],
                "points": ["- (폴백) 기사 원문 참조", "- (폴백) 요약은 추후 제공"],
                "insight": "☞ (폴백) 최신 링크 카드"
            })
# 기존: not data → latest.json 또는 후보로 채우기  ❌
# 운영 모드: 그냥 빈 배열 저장(프론트는 '데이터 없음' 표시)  ✅
# 아래 블록을 주석 처리하세요.
# if not data:
#     print("Empty result → fallback to latest or candidates")
#     ...

# 그대로 저장
with open(OUTFILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

    main()
