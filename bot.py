import os
import requests
from bs4 import BeautifulSoup

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# 영양제 갤러리 특가판매 탭 전체 목록 URL
TARGET_URL = "https://gall.dcinside.com/mgallery/board/lists/?id=nutrient&sort_type=N&search_head=10&page=1"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

RECORD_FILE = "sent_posts.txt"

def load_sent_posts():
    if not os.path.exists(RECORD_FILE):
        return set()
    with open(RECORD_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip().isdigit())

def save_sent_posts(post_ids):
    recent_list = list(post_ids)[-100:]
    with open(RECORD_FILE, "w", encoding="utf-8") as f:
        for pid in recent_list:
            f.write(f"{pid}\n")

def send_discord_alarm(title, link):
    payload = {
        "content": f"📢 **[영양제 갤러리 특가 알림]**\n**제목:** {title}\n**링크:** {link}"
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"디스코드 전송 실패: {e}")

def check_new_posts():
    sent_ids = load_sent_posts()
    
    response = requests.get(TARGET_URL, headers=HEADERS)
    if response.status_code != 200:
        print(f"페이지 요청 실패: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    rows = soup.select('tr.ub-content.us-post')

    new_posts_found = False
    for row in reversed(rows):
        num_tag = row.select_one('.gall_num')
        if not num_tag:
            continue
            
        post_id = num_tag.text.strip()
        if not post_id.isdigit():
            continue

        if post_id in sent_ids:
            continue

        title_tag = row.select_one('.gall_tit a')
        if not title_tag:
            continue

        title = title_tag.text.strip()
        link = "https://gall.dcinside.com" + title_tag['href']

        # 첫 실행 이후 등록되는 새 글만 디스코드로 알림
        if len(sent_ids) > 0:
            send_discord_alarm(title, link)
            print(f"[알림 전송] {title}")

        sent_ids.add(post_id)
        new_posts_found = True

    if new_posts_found:
        save_sent_posts(sent_ids)

if __name__ == "__main__":
    check_new_posts()
