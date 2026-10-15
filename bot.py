import os
import sys
import time
import json
import re
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
BASE_URL = "https://gall.dcinside.com"
TARGET_URL = f"{BASE_URL}/mgallery/board/lists/?id=nutrient&sort_type=N&search_head=10&page=1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": f"{BASE_URL}/mgallery/board/lists/?id=nutrient",
}

RECORD_FILE = "sent_posts.txt"
STATE_FILE = "bot_state.json"
KEEP = 200
FAIL_THRESHOLD = 3    # 연속 3회 실행 실패 시 경고 (약 15분)
ROUNDS = 4
INTERVAL = 60

def load_sent_posts():
    first_run = not os.path.exists(RECORD_FILE)
    if first_run:
        return set(), True
    with open(RECORD_FILE, "r", encoding="utf-8") as f:
        ids = {int(line.strip()) for line in f if line.strip().isdigit()}
    return ids, False

def save_sent_posts(post_ids):
    recent = sorted(post_ids)[-KEEP:]
    with open(RECORD_FILE, "w", encoding="utf-8") as f:
        for pid in recent:
            f.write(f"{pid}\n")

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"consecutive_failures": 0, "alerted": False}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def sanitize(msg):
    msg = str(msg)
    if DISCORD_WEBHOOK_URL:
        msg = msg.replace(DISCORD_WEBHOOK_URL, "[웹훅URL]")
    msg = re.sub(r"https://discord(?:app)?\.com/api/webhooks/\S+", "[웹훅URL]", msg)
    return msg.replace("`", "'")[:300]

def send_discord_alarm(title, link):
    payload = {
        "content": f"📢 **[영양제 갤러리 특가 알림]**\n**제목:** {title}\n**링크:** {link}",
        "allowed_mentions": {"parse": []},
    }
    for _ in range(3):
        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        except requests.RequestException as e:
            print(f"디스코드 전송 오류: {e}")
            time.sleep(2)
            continue
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2)))
            continue
        if r.ok:
            return True
        print(f"디스코드 응답 오류: {r.status_code}")
        return False
    return False

def send_status_alarm(text):
    """성공하면 True, 실패하면 False를 반환"""
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

def check_new_posts():
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL 이 설정되지 않았습니다.")
        
    sent_ids, first_run = load_sent_posts()
    
    try:
        response = requests.get(TARGET_URL, headers=HEADERS, timeout=15)
    except requests.RequestException as e:
        raise RuntimeError(f"페이지 요청 오류: {e}")
        
    if response.status_code != 200:
        raise RuntimeError(f"페이지 요청 실패: {response.status_code}")
        
    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.select("tr.ub-content.us-post")
    if not rows:
        raise RuntimeError("게시글 행을 찾지 못했습니다 (차단 또는 구조 변경 가능성).")
        
    changed = False
    for row in reversed(rows):
        num_tag = row.select_one(".gall_num")
        if not num_tag or not num_tag.text.strip().isdigit():
            continue
            
        post_id = int(num_tag.text.strip())
        if post_id in sent_ids:
            continue
            
        title_tag = row.select_one(".gall_tit a")
        if not title_tag or not title_tag.get("href"):
            continue
            
        title = title_tag.text.strip()
        link = urljoin(BASE_URL, title_tag["href"])
        
        if not first_run:
            if not send_discord_alarm(title, link):
                continue
            print(f"[알림 전송] {title}")
            time.sleep(0.5)
            
        sent_ids.add(post_id)
        changed = True
        
    if changed:
        save_sent_posts(sent_ids)

if __name__ == "__main__":
    failures = 0
    last_error = ""
    for i in range(ROUNDS):
        try:
            check_new_posts()
        except Exception as e:
            failures += 1
            last_error = sanitize(e)
            print(f"[{i+1}/{ROUNDS}] 크롤링/처리 오류: {last_error}")
            
        if i < ROUNDS - 1:
            time.sleep(INTERVAL)

    state = load_state()
    run_failed = (failures == ROUNDS)

    if run_failed:
        state["consecutive_failures"] += 1
        if state["consecutive_failures"] >= FAIL_THRESHOLD and not state["alerted"]:
            sent = send_status_alarm(
                "🚨 **[영양제 갤러리 알리미 봇 오류]**\n"
                f"연속 {state['consecutive_failures']}회 실행 실패\n"
                f"마지막 원인: `{last_error}`\n"
                "Actions 탭 확인이 필요합니다."
            )
            state["alerted"] = sent  # 전송 실패 시 다음 실행에서 재시도
    else:
        if state["alerted"]:
            send_status_alarm("✅ **[영양제 갤러리 알리미 봇]** 정상 작동으로 복구되었습니다.")
        state = {"consecutive_failures": 0, "alerted": False}

    save_state(state)

    if run_failed:
        sys.exit(1)
