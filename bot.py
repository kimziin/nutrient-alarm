import os
import re
import sys
import json
import time
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
BASE_URL = "https://gall.dcinside.com"

# ── 크롤링 대상 ─────────────────────────────────────────────
# key  : 저장용 고유 접두어 (영문/숫자만, 언더스코어 금지, 한 번 정하면 변경 금지)
# name : 상태/오류 알림에 쓰는 갤러리 이름
# label: 디스코드 알림 머리말
TARGETS = [
    {
        "key": "nutrient",
        "name": "헬스보충제 갤",
        "label": "헬스보충제 갤 특가 알림",
        "url": f"{BASE_URL}/mgallery/board/lists/?id=nutrient&sort_type=N&search_head=10&page=1",
    },
    {
        "key": "mounjaro",
        "name": "마운자로 갤",
        "label": "마운자로 갤 핫딜 알림",
        "url": f"{BASE_URL}/mgallery/board/lists/?id=mounjaro&sort_type=N&search_head=80&page=1",
    },
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

RECORD_FILE = "sent_posts.txt"
STATE_FILE = "bot_state.json"
KEEP = 200            # 갤러리별 보존 개수
ROUNDS = 4            # 한 번 실행에서의 회차 수
INTERVAL = 60         # 회차 시작 간격(초)
TARGET_GAP = 2        # 갤러리 간 요청 간격(초)
FAIL_THRESHOLD = 3    # 연속 3회 실행 실패 시 경고 (약 15분)

# ── 공통 유틸 ───────────────────────────────────────────────
def sanitize(msg):
    msg = str(msg)
    if DISCORD_WEBHOOK_URL:
        msg = msg.replace(DISCORD_WEBHOOK_URL, "[웹훅URL]")
    msg = re.sub(r"https://discord(?:app)?\.com/api/webhooks/\S+", "[웹훅URL]", msg)
    return msg.replace("`", "'")[:300]

# ── 중복 방지 기록 (키: "갤러리key_글번호") ─────────────────
def load_sent_posts():
    sent = set()
    if not os.path.exists(RECORD_FILE):
        return sent
    with open(RECORD_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.isdigit():
                # 구버전 형식(숫자만)은 nutrient 갤러리 기록으로 간주
                sent.add(f"nutrient_{line}")
            elif re.fullmatch(r"[A-Za-z0-9]+_\d+", line):
                sent.add(line)
    return sent

def save_sent_posts(sent):
    groups = {}
    for k in sent:
        prefix, _, num = k.rpartition("_")
        groups.setdefault(prefix, []).append(int(num))
    with open(RECORD_FILE, "w", encoding="utf-8") as f:
        for prefix in sorted(groups):
            for n in sorted(groups[prefix])[-KEEP:]:   # 갤러리별 숫자 기준 최신 N개
                f.write(f"{prefix}_{n}\n")

# ── 디스코드 전송 ───────────────────────────────────────────
def send_discord_alarm(label, title, link):
    payload = {
        "content": f"📢 **[{label}]**\n**제목:** {title}\n**링크:** {link}",
        "allowed_mentions": {"parse": []},
    }
    for _ in range(3):
        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        except requests.RequestException as e:
            print(f"디스코드 전송 오류: {sanitize(e)}")
            time.sleep(2)
            continue
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("Retry-After", 2))
            except ValueError:
                wait = 2
            time.sleep(min(wait, 10))
            continue
        if r.ok:
            return True
        print(f"디스코드 응답 오류: {r.status_code}")
        return False
    return False

def send_status_alarm(text):
    """성공하면 True, 실패하면 False"""
    if not DISCORD_WEBHOOK_URL:
        return False
    payload = {"content": text, "allowed_mentions": {"parse": []}}
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if not r.ok:
            print(f"상태 알림 응답 오류: {r.status_code}")
        return r.ok
    except requests.RequestException as e:
        print(f"상태 알림 전송 실패: {sanitize(e)}")
        return False

# ── 갤러리 1개 크롤링 ───────────────────────────────────────
def check_gallery(target):
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL 이 설정되지 않았습니다.")
        
    key, label, url = target["key"], target["label"], target["url"]
    prefix = f"{key}_"
    sent = load_sent_posts()
    
    # 이 갤러리의 기록이 하나도 없으면 최초 실행: 알림 없이 기록만
    first_run = not any(k.startswith(prefix) for k in sent)
    
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": url.split("&")[0],
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as e:
        raise RuntimeError(f"[{key}] 페이지 요청 오류: {sanitize(e)}")
        
    if response.status_code != 200:
        raise RuntimeError(f"[{key}] 페이지 요청 실패: HTTP {response.status_code}")
        
    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.select("tr.ub-content.us-post")
    if not rows:
        raise RuntimeError(f"[{key}] 게시글 행을 찾지 못했습니다 (차단 또는 구조 변경 가능성)")
        
    changed = False
    for row in reversed(rows):  # 오래된 글부터
        num_tag = row.select_one(".gall_num")
        if not num_tag or not num_tag.text.strip().isdigit():
            continue  # 공지/광고 등 제외
            
        post_key = f"{prefix}{int(num_tag.text.strip())}"
        if post_key in sent:
            continue
            
        title_tag = row.select_one(".gall_tit a")
        if not title_tag or not title_tag.get("href"):
            continue
            
        title = title_tag.text.strip()
        link = urljoin(BASE_URL, title_tag["href"])
        
        if not first_run:
            if not send_discord_alarm(label, title, link):
                continue  # 실패 시 기록하지 않아 다음 회차에 재시도
            print(f"[알림 전송][{key}] {title}")
            time.sleep(0.5)
            
        sent.add(post_key)
        changed = True
        
    if changed:
        save_sent_posts(sent)
        if first_run:
            print(f"[{key}] 최초 실행: 기존 글 기록만 저장")

# ── 상태 파일 (갤러리별 연속 실패 추적) ─────────────────────
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        raw = {}
        
    old_targets = raw.get("targets", {}) if isinstance(raw, dict) else {}
    return {
        "targets": {
            t["key"]: {
                "consecutive_failures": old_targets.get(t["key"], {}).get("consecutive_failures", 0),
                "alerted": old_targets.get(t["key"], {}).get("alerted", False),
            }
            for t in TARGETS
        }
    }

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ── 메인 ────────────────────────────────────────────────────
if __name__ == "__main__":
    failures = {t["key"]: 0 for t in TARGETS}
    last_errors = {t["key"]: "" for t in TARGETS}
    
    for i in range(ROUNDS):
        round_start = time.monotonic()
        for j, t in enumerate(TARGETS):
            try:
                check_gallery(t)
            except Exception as e:
                failures[t["key"]] += 1
                last_errors[t["key"]] = sanitize(e)
                print(f"[{i+1}/{ROUNDS}] {t['name']} 오류: {last_errors[t['key']]}")
                
            if j < len(TARGETS) - 1:
                time.sleep(TARGET_GAP)
                
        if i < ROUNDS - 1:
            elapsed = time.monotonic() - round_start
            time.sleep(max(1, INTERVAL - elapsed))

    state = load_state()
    any_run_failed = False
    
    for t in TARGETS:
        key = t["key"]
        st = state["targets"][key]
        run_failed = failures[key] == ROUNDS   # 이 갤러리가 모든 회차에서 실패
        
        if run_failed:
            any_run_failed = True
            st["consecutive_failures"] += 1
            if st["consecutive_failures"] >= FAIL_THRESHOLD and not st["alerted"]:
                sent_ok = send_status_alarm(
                    f"🚨 **[{t['name']} 알리미 오류]**\n"
                    f"연속 {st['consecutive_failures']}회 실행 실패\n"
                    f"마지막 원인: `{last_errors[key]}`\n"
                    "Actions 탭 확인이 필요합니다."
                )
                st["alerted"] = sent_ok   # 전송 실패 시 다음 실행에서 재시도
        else:
            if st["alerted"]:
                send_status_alarm(f"✅ **[{t['name']} 알리미]** 정상 작동으로 복구되었습니다.")
            st["consecutive_failures"] = 0
            st["alerted"] = False
            
    save_state(state)
    
    if any_run_failed:
        sys.exit(1)
