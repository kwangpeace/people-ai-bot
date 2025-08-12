# 필요한 라이브러리들을 가져옵니다.
import os
import re
import json
import random
import logging
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2 import service_account
from datetime import datetime
import pytz

import google.generativeai as genai
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

# --- 환경 변수 체크 ---
required_env = [
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "GEMINI_API_KEY",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDENTIALS_JSON",
    "GITHUB_TOKEN" # (추가) GitHub 연동을 위한 토큰
]
for key in required_env:
    if not os.environ.get(key):
        logging.critical(f"환경 변수 '{key}'가 설정되지 않았습니다. 앱을 시작할 수 없습니다.")
        exit()

# --- 로깅(기록) 설정 ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

# --- 앱 초기화 ---
try:
    app = App(
        token=os.environ.get("SLACK_BOT_TOKEN"),
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
    )
    flask_app = Flask(__name__)
    handler = SlackRequestHandler(app)
    logger.info("Slack App 및 Flask 앱 초기화 성공")
except Exception as e:
    logger.critical(f"앱 초기화 실패: {e}")
    exit()

# --- 메인 봇 클래스 ---
class PeopleAIBot:
    def __init__(self):
        try:
            auth_test = app.client.auth_test()
            self.bot_id = auth_test['user_id']
            logger.info(f"봇 ID({self.bot_id})를 성공적으로 가져왔습니다.")
        except Exception as e:
            logger.error(f"봇 ID 가져오기 실패: {e}")
            self.bot_id = None

        # (개선) Railway 환경에서 안정적으로 작동하도록 GitHub에서 파일을 로드합니다.
        github_repo = "kwangpeace/people-ai-bot" # 본인의 '사용자이름/저장소이름'으로 수정
        self.knowledge_base = self.load_data_from_github(github_repo, "guide_data.txt")
        self.help_text = self.load_data_from_github(github_repo, "help.md", "도움말 파일을 찾을 수 없습니다.")

        self.gemini_model = self.setup_gemini()
        self.worksheet = self.setup_google_sheets()
        self.responses = {"searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔"]}

    def load_data_from_github(self, repo, path, default_text=""):
        """GitHub Private 저장소에서 파일 내용을 읽어옵니다."""
        token = os.environ.get("GITHUB_TOKEN")
        url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
        headers = {"Authorization": f"token {token}"}
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                logger.info(f"GitHub에서 '{path}' 파일을 성공적으로 로드했습니다.")
                return response.text
            else:
                logger.error(f"GitHub 파일 로드 실패. 상태 코드: {response.status_code}")
                return default_text
        except Exception as e:
            logger.error(f"GitHub 파일 로드 중 오류 발생: {e}")
            return default_text

    def setup_google_sheets(self):
        """Google Sheets API를 설정하고 워크시트를 반환합니다."""
        try:
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds_json_str = os.environ.get("GOOGLE_CREDENTIALS_JSON")
            creds_info = json.loads(creds_json_str)
            creds = service_account.Credentials.from_service_account_info(creds_info, scopes=scopes)
            client = gspread.authorize(creds)
            sheet_id = os.environ.get("GOOGLE_SHEET_ID")
            spreadsheet = client.open_by_key(sheet_id)
            worksheet = spreadsheet.worksheet("도서주문")
            logger.info("Google Sheets '도서주문' 시트 초기화 성공.")
            return worksheet
        except Exception as e:
            logger.critical(f"Google Sheets 초기화 실패: {e}", exc_info=True)
            return None

    def extract_book_info(self, url):
        """교보문고 URL에서 책 제목, 저자, ISBN 정보를 추출합니다."""
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            
            title_elem = soup.select_one('span.prod_title_text, h1.prod_title, h1.title')
            title = title_elem.get_text(strip=True) if title_elem else "제목을 찾을 수 없습니다."
            
            author_elem = soup.select_one('a.author, span.author')
            author = author_elem.get_text(strip=True) if author_elem else "저자를 찾을 수 없습니다."

            isbn = "ISBN 정보 없음"
            for tr in soup.select("div.prod_detail_area_bottom table tr"):
                th = tr.find("th")
                if th and "ISBN" in th.get_text():
                    td = tr.find("td")
                    if td:
                        isbn = td.get_text(strip=True)
                    break
            
            return {"title": title, "author": author, "url": url, "isbn": isbn}
        except Exception as e:
            logger.error(f"도서 정보 추출 중 오류 발생: {e}")
            return None

    def add_book_to_sheet(self, book_info, user_name, request_time):
        """추출된 도서 정보를 신청자 정보와 함께 구글 시트에 추가합니다."""
        if not self.worksheet:
            return False
        try:
            # (개선) ISBN 정보까지 함께 기록합니다.
            self.worksheet.append_row([
                book_info['title'],
                book_info['author'],
                book_info['isbn'],
                book_info['url'],
                user_name,
                request_time
            ])
            logger.info(f"'{book_info['title']}'을(를) 구글 시트에 추가했습니다.")
            return True
        except Exception as e:
            logger.error(f"구글 시트 추가 실패: {e}")
            return False

    def setup_gemini(self):
        # ... (기존과 동일) ...
        try:
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            return genai.GenerativeModel("gemini-1.5-flash")
        except Exception as e:
            return None

    def generate_answer(self, query):
        # ... (기존과 동일) ...
        return "AI 답변"

# 봇 인스턴스 생성
bot = PeopleAIBot()

# --- 기능별 함수 분리 ---
def handle_book_request(event, say):
    """'도서신청' 명령어를 처리하는 전용 함수"""
    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts", event.get("ts"))
    user_id = event.get("user")
    text = event.get("text", "").strip()
    
    url_match = re.search(r"https?://\S+", text)
    if not url_match:
        say(text="⚠️ 도서신청 명령어와 함께 교보문고 URL을 입력해주세요.", thread_ts=thread_ts)
        return
    
    url = url_match.group(0)
    processing_msg = say(text=f"✅ 도서 신청을 접수했습니다. 잠시 링크를 분석할게요...", thread_ts=thread_ts)
    
    book_info = bot.extract_book_info(url)
    if book_info and book_info["title"] != "제목을 찾을 수 없습니다.":
        user_name = "알수없음"
        try:
            user_info_response = app.client.users_info(user=user_id)
            user_name = user_info_response["user"]["profile"].get("real_name", user_id)
        except Exception as e:
            logger.error(f"Slack 사용자 정보 조회 실패: {e}")

        kst = pytz.timezone('Asia/Seoul')
        request_time = datetime.now(kst).strftime('%Y-%m-%d %H:%M:%S')
        
        success = bot.add_book_to_sheet(book_info, user_name, request_time)
        
        if success:
            # (개선) 완료 메시지에 ISBN 정보도 함께 보여줍니다.
            reply_text = (f"📚 *도서 신청이 완료되었습니다!*\n\n"
                          f"• *책 제목:* {book_info['title']}\n"
                          f"• *저자:* {book_info['author']}\n"
                          f"• *ISBN:* {book_info['isbn']}\n"
                          f"• *신청자:* {user_name}\n\n"
                          f"🔗 구글 시트에 정상적으로 기록했습니다.")
        else:
            reply_text = "⚠️ 구글 시트에 기록하는 중 문제가 발생했습니다. 피플팀에 문의해주세요."
    else:
        reply_text = "⚠️ 해당 링크에서 도서 정보를 찾을 수 없습니다. 교보문고 상품 상세 링크가 맞는지 확인해주세요."
    
    app.client.chat_update(channel=channel_id, ts=processing_msg['ts'], text=reply_text)


@app.event("message")
def handle_message_events(body, say):
    # ... (기존과 거의 동일, 분기 처리 로직) ...
    # ... 핸들러가 handle_book_request, handle_general_query 등을 호출 ...

# --- Flask 앱 라우팅 및 앱 실행 ---
# ... (기존과 동일) ...
