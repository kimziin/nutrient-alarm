import os
import sys
import time
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
KEEP = 200

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

ROUNDS = 4        # 0초, 60초, 120초, 180초 총 4회 실행
INTERVAL = 60     # 초

if __name__ == "__main__":
    failures = 0
    for i in range(ROUNDS):
        try:
            check_new_posts()
        except Exception as e:
            failures += 1
            print(f"[{i+1}/{ROUNDS}] 오류: {e}")
            
        if i < ROUNDS - 1:
            time.sleep(INTERVAL)
            
    # 모든 회차가 전부 실패했을 때만 에러 처리
    if failures == ROUNDS:
        sys.exit(1)
