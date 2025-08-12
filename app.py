import os
import re
import json
import random
import logging
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2 import service_account

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
    "GOOGLE_CREDENTIALS"  # credentials.json 대신 환경 변수 추가
]
for key in required_env:
    if not os.environ.get(key):
        logging.critical(f"환경 변수 '{key}'가 설정되지 않았습니다. 앱을 시작할 수 없습니다.")
        exit()

# --- 로깅 설정 ---
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
            self.bot_id = app.client.auth_test()['user_id']
            logger.info(f"봇 ID({self.bot_id})를 성공적으로 가져왔습니다.")
        except Exception as e:
            logger.error(f"봇 ID 가져오기 실패: {e}")
            self.bot_id = None

        self.gemini_model = self.setup_gemini()
        self.worksheet = self.setup_google_sheets()  # 구글 시트 설정 추가
        self.knowledge_base = self.load_knowledge_file()
        self.help_text = self.load_help_file()
        self.responses = {"searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔"]}
        self.setup_direct_answers()

    def setup_google_sheets(self):
        """Google Sheets API를 설정하고 워크시트를 반환합니다."""
        try:
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds_json_str = os.environ.get("GOOGLE_CREDENTIALS")  # GOOGLE_CREDENTIALS 사용

            if creds_json_str:
                logger.info("환경 변수에서 Google 인증 정보를 로드합니다.")
                creds_info = json.loads(creds_json_str)
                creds = service_account.Credentials.from_service_account_info(creds_info, scopes=scopes)
            else:
                logger.info("로컬 'credentials.json' 파일에서 Google 인증 정보를 로드합니다.")
                creds = service_account.Credentials.from_service_account_file("credentials.json", scopes=scopes)

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
        """교보문고 URL에서 책 제목과 저자 정보를 추출합니다."""
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            title_elem = soup.select_one('h1.prod_title, h1.title')
            title = title_elem.get_text(strip=True) if title_elem else "제목을 찾을 수 없습니다."
            author_elem = soup.select_one('span.author')
            author = author_elem.get_text(strip=True) if author_elem else "저자를 찾을 수 없습니다."
            logger.info(f"도서 정보 추출 성공: {title} / {author}")
            return {"title": title, "author": author, "url": url}
        except Exception as e:
            logger.error(f"도서 정보 추출 중 오류 발생: {e}")
            return None

    def add_book_to_sheet(self, book_info):
        """추출된 도서 정보를 구글 시트에 추가합니다."""
        if not self.worksheet:
            logger.error("워크시트가 설정되지 않아 도서 정보를 추가할 수 없습니다.")
            return False
        try:
            self.worksheet.append_row([book_info['title'], book_info['author'], book_info['url']])
            logger.info(f"'{book_info['title']}'을(를) 구글 시트에 추가했습니다.")
            return True
        except Exception as e:
            logger.error(f"구글 시트 추가 실패: {e}")
            return False

    def setup_direct_answers(self):
        """AI를 거치지 않고 즉시 답변할 특정 질문과 답변을 설정합니다."""
        self.direct_answers = [
            {
                "keywords": ["외부 회의실", "외부회의실", "스파크플러스 예약", "4층 회의실"],
                "answer": """피플팀에서 예약 가능 여부를 확인한 후, 이 스레드로 답변을 드릴게요. (@김정수)"""
            }
        ]
        logger.info("특정 질문에 대한 직접 답변(치트키) 설정 완료.")

    def setup_gemini(self):
        try:
            gemini_api_key = os.environ.get("GEMINI_API_KEY")
            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            logger.info("Gemini API 활성화 완료.")
            return model
        except Exception as e:
            logger.error(f"Gemini 모델 설정 실패: {e}")
            return None

    def load_knowledge_file(self):
        try:
            with open("guide_data.txt", 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.error("'guide_data.txt' 파일을 찾을 수 없습니다.")
            return ""

    def load_help_file(self):
        try:
            with open("help.md", 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.error("'help.md' 파일을 찾을 수 없습니다.")
            return "도움말 파일을 찾을 수 없습니다."

    def generate_answer(self, query):
        for item in self.direct_answers:
            for keyword in item["keywords"]:
                if keyword in query:
                    logger.info(f"'{keyword}' 키워드를 감지하여 지정된 답변을 반환합니다.")
                    return item["answer"]

        if not self.gemini_model: return "AI 모델이 설정되지 않아 답변할 수 없습니다."
        if not self.knowledge_base: return "지식 파일이 비어있어 답변할 수 없습니다."
        
        prompt = f"""
        [당신의 역할]
        당신은 '중고나라' 회사의 피플팀 AI 어시스턴트 '피플AI'입니다. 당신의 임무는 동료의 질문에 명확하고 간결하며, 가독성 높은 답변을 제공하는 것입니다.
        
        [답변 생성 원칙]
        (기존의 긴 프롬프트 내용)
        ---
        [참고 자료]
        {self.knowledge_base}
        ---
        [질문]
        {query}
        [답변]
        """
        try:
            response = self.gemini_model.generate_content(prompt)
            if not response.text.strip():
                logger.warning("Gemini API가 비어있는 응답을 반환했습니다.")
                return "답변을 생성하는 데 조금 시간이 걸리고 있어요. 다시 한 번 시도해주시겠어요?"
            
            logger.info(f"Gemini 답변 생성 성공. (쿼리: {query[:30]}...)")
            return response.text
        except Exception as e:
            logger.error(f"Gemini API 호출 실패: {e}", exc_info=True)
            return "음... 답변을 생성하는 도중 문제가 발생했어요. 잠시 후 다시 시도해보시겠어요? 😢"

bot = PeopleAIBot()

def handle_new_message(event, say):
    """스레드 밖의 새로운 메시지를 처리합니다."""
    channel_id = event.get("channel")
    text = event.get("text", "").strip().replace(f"<@{bot.bot_id}>", "").strip()
    message_ts = event.get("ts")
    
    if not text: return

    logger.info("새로운 메시지를 감지했습니다. 스레드를 시작하며 답변합니다.")
    thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=message_ts)
    final_answer = bot.generate_answer(text)
    app.client.chat_update(channel=channel_id, ts=thinking_message['ts'], text=final_answer)

def handle_thread_reply(event, say):
    """스레드 내의 답글을 처리합니다."""
    text = event.get("text", "")
    if f"<@{bot.bot_id}>" in text:
        logger.info("스레드 내에서 멘션을 감지하여 응답합니다.")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts")
        clean_query = text.replace(f"<@{bot.bot_id}>", "").strip()
        if not clean_query: return

        thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=thread_ts)
        final_answer = bot.generate_answer(clean_query)
        app.client.chat_update(channel=channel_id, ts=thinking_message['ts'], text=final_answer)

@app.event("message")
def handle_all_message_events(body, say, logger):
    try:
        event = body["event"]
        if "subtype" in event or (bot.bot_id and event.get("user") == bot.bot_id):
            return

        text = event.get("text", "").strip()
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts", event.get("ts"))
        message_ts = event.get("ts")

        if text == "도움말":
            logger.info(f"'{event.get('user')}' 사용자가 도움말을 요청했습니다.")
            reply_ts = thread_ts if thread_ts else message_ts
            say(text=bot.help_text, thread_ts=reply_ts)
            return

        if f"<@{bot.bot_id}>" in text:
            if event.get("thread_ts"):
                handle_thread_reply(event, say)
            else:
                handle_new_message(event, say)

    except Exception as e:
        logger.error(f"message 이벤트 처리 중 오류 발생: {e}", exc_info=True)

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check():
    return "PeopleAI Bot is running! 🟢"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
