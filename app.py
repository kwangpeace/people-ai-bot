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
import pytz # (추가) 시간대 처리를 위한 라이브러리

import google.generativeai as genai
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

# --- 환경 변수 체크 ---
# GOOGLE_CREDENTIALS_JSON 변수도 필수로 체크합니다.
required_env = [
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "GEMINI_API_KEY",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDENTIALS_JSON"
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

        self.gemini_model = self.setup_gemini()
        self.worksheet = self.setup_google_sheets()
        self.knowledge_base = self.load_knowledge_file("guide_data.txt")
        self.help_text = self.load_knowledge_file("help.md", "도움말 파일을 찾을 수 없습니다.")
        self.responses = {"searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔"]}
        self.setup_direct_answers()

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
            worksheet = spreadsheet.worksheet("도서주문") # '도서주문' 탭을 사용
            logger.info("Google Sheets '도서주문' 시트 초기화 성공.")
            return worksheet
        except gspread.exceptions.WorksheetNotFound:
            logger.critical("스프레드시트에서 '도서주문' 워크시트를 찾을 수 없습니다.")
            return None
        except Exception as e:
            logger.critical(f"Google Sheets 초기화 실패: {e}", exc_info=True)
            return None

    def extract_book_info(self, url):
        """교보문고 URL에서 책 정보를 추출합니다."""
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            
            title_elem = soup.select_one('h1.prod_title, h1.title')
            title = title_elem.get_text(strip=True) if title_elem else "제목을 찾을 수 없습니다."
            
            author_elem = soup.select_one('span.author')
            author = author_elem.get_text(strip=True) if author_elem else "저자를 찾을 수 없습니다."
            
            return {"title": title, "author": author, "url": url}
        except Exception as e:
            logger.error(f"도서 정보 추출 중 오류 발생: {e}")
            return None

    # (개선) 신청자 이름과 신청 시간을 함께 받도록 함수 수정
    def add_book_to_sheet(self, book_info, user_name, request_time):
        """추출된 도서 정보를 신청자 정보와 함께 구글 시트에 추가합니다."""
        if not self.worksheet:
            logger.error("워크시트가 설정되지 않아 도서 정보를 추가할 수 없습니다.")
            return False
        try:
            # 제목, 저자, URL, 신청자, 신청일 순서로 기록
            self.worksheet.append_row([
                book_info['title'],
                book_info['author'],
                book_info['url'],
                user_name,
                request_time
            ])
            logger.info(f"'{book_info['title']}'을(를) 구글 시트에 추가했습니다. (신청자: {user_name})")
            return True
        except Exception as e:
            logger.error(f"구글 시트 추가 실패: {e}")
            return False

    def setup_direct_answers(self):
        """AI를 거치지 않고 즉시 답변할 특정 질문과 답변을 설정합니다."""
        self.direct_answers = [
            {"keywords": ["외부 회의실", "외부회의실"], "answer": "피플팀에서 예약 가능 여부 확인 후 스레드로 답변 드릴게요."}
        ]
        logger.info("특정 질문에 대한 직접 답변 설정 완료.")

    def setup_gemini(self):
        try:
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            model = genai.GenerativeModel("gemini-1.5-flash")
            logger.info("Gemini API 활성화 완료.")
            return model
        except Exception as e:
            logger.error(f"Gemini 모델 설정 실패: {e}")
            return None

    def load_knowledge_file(self, filename, error_message=""):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.error(f"'{filename}' 파일을 찾을 수 없습니다.")
            return error_message

    def generate_answer(self, query):
        # ... (기존과 동일) ...
        for item in self.direct_answers:
            if any(keyword in query for keyword in item["keywords"]):
                return item["answer"]
        if not self.gemini_model: return "AI 모델이 설정되지 않아 답변할 수 없습니다."
        prompt = f"..."
        try:
            response = self.gemini_model.generate_content(prompt)
            return response.text
        except Exception as e:
            return "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 😢"

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
        try:
            user_info_response = app.client.users_info(user=user_id)
            user_name = user_info_response["user"]["profile"].get("real_name", user_id)
        except Exception as e:
            logger.error(f"Slack 사용자 정보 조회 실패: {e}")
            user_name = "알수없음"

        kst = pytz.timezone('Asia/Seoul')
        request_time = datetime.now(kst).strftime('%Y-%m-%d %H:%M:%S')
        
        success = bot.add_book_to_sheet(book_info, user_name, request_time)
        
        if success:
            reply_text = (f"📚 *도서 신청이 완료되었습니다!*\n\n"
                          f"• *책 제목:* {book_info['title']}\n"
                          f"• *저자:* {book_info['author']}\n"
                          f"• *신청자:* {user_name}\n\n"
                          f"🔗 구글 시트에 정상적으로 기록했습니다.")
        else:
            reply_text = "⚠️ 구글 시트에 기록하는 중 문제가 발생했습니다. 피플팀에 문의해주세요."
    else:
        reply_text = "⚠️ 해당 링크에서 도서 정보를 찾을 수 없습니다. 교보문고 상품 상세 링크가 맞는지 확인해주세요."
    
    app.client.chat_update(channel=channel_id, ts=processing_msg['ts'], text=reply_text)

def handle_general_query(event, say):
    """AI를 통해 일반적인 질문에 답변하는 함수"""
    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts", event.get("ts"))
    query = event.get("text", "").replace(f"<@{bot.bot_id}>", "").strip()

    if not query: return

    thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=thread_ts)
    final_answer = bot.generate_answer(query)
    app.client.chat_update(channel=channel_id, ts=thinking_message['ts'], text=final_answer)

# --- 메인 이벤트 핸들러 ---
@app.event("message")
def handle_message_events(body, say):
    """모든 메시지 이벤트를 수신하고 적절한 핸들러로 분기합니다."""
    try:
        event = body["event"]
        if "subtype" in event or (bot.bot_id and event.get("user") == bot.bot_id):
            return

        text = event.get("text", "").strip()
        
        # 봇을 멘션한 경우에만 반응
        if f"<@{bot.bot_id}>" in text:
            clean_query = text.replace(f"<@{bot.bot_id}>", "").strip()

            # '도서신청' 명령어가 포함되어 있으면 도서 신청 함수 호출
            if "도서신청" in clean_query:
                handle_book_request(event, say)
            # '도움말' 명령어 처리
            elif clean_query == "도움말":
                say(text=bot.help_text, thread_ts=event.get("ts"))
            # 그 외 모든 멘션은 일반 질문으로 간주하여 AI 답변 처리
            else:
                handle_general_query(event, say)

    except Exception as e:
        logger.error(f"message 이벤트 처리 중 오류 발생: {e}", exc_info=True)

# --- Flask 앱 라우팅 ---
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check():
    return "PeopleAI Bot is running! 🟢"

# --- 앱 실행 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
