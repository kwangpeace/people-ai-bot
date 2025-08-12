# -*- coding: utf-8 -*-
# 필요한 라이브러리들을 가져옵니다.
import os
import random
import logging
import re
import json
from datetime import datetime

# 웹 관련 라이브러리 (requests, beautifulsoup4는 이제 필요 없습니다)
# playwright도 필요 없습니다.

# AI 및 슬랙, 구글 시트 관련 라이브러리
import google.generativeai as genai
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

import gspread
from google.oauth2.service_account import Credentials

# --- 환경 변수 체크 ---
required_env = [
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "GEMINI_API_KEY",
    "GOOGLE_CREDENTIALS_JSON",
    "GOOGLE_SHEET_ID"
]
for key in required_env:
    if not os.environ.get(key):
        logging.critical(f"환경 변수 '{key}'가 설정되지 않았습니다. 앱을 시작할 수 없습니다.")
        exit()

# --- 로깅 설정 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

# --- 구글 시트 클라이언트 초기화 ---
def setup_gspread_client():
    try:
        creds_json_str = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        creds_info = json.loads(creds_json_str)
        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        logger.info("구글 시트 클라이언트 초기화 성공")
        return client
    except Exception as e:
        logger.critical(f"구글 시트 클라이언트 초기화 실패: {e}"); return None

gs_client = setup_gspread_client()

# --- 앱 초기화 (다시 안정적인 동기 방식으로) ---
app = App(token=os.environ.get("SLACK_BOT_TOKEN"), signing_secret=os.environ.get("SLACK_SIGNING_SECRET"))
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

# --- 메인 봇 클래스 ---
class PeopleAIBot:
    def __init__(self):
        try:
            self.bot_id = app.client.auth_test()['user_id']
            logger.info(f"봇 ID({self.bot_id})를 성공적으로 가져왔습니다.")
        except Exception as e:
            logger.error(f"봇 ID 가져오기 실패: {e}"); self.bot_id = None
        
        self.gemini_model = self.setup_gemini()
        self.knowledge_base = self.load_knowledge_file()
        self.help_text = self.load_help_file()
        self.responses = { "searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔"] }

    def setup_gemini(self):
        try:
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            return genai.GenerativeModel("gemini-1.5-flash")
        except Exception as e:
            logger.error(f"Gemini 모델 설정 실패: {e}"); return None

    def load_knowledge_file(self):
        try:
            with open("guide_data.txt", 'r', encoding='utf-8') as f: return f.read()
        except FileNotFoundError: return ""

    def load_help_file(self):
        try:
            with open("help.md", 'r', encoding='utf-8') as f: return f.read()
        except FileNotFoundError: return "도움말 파일을 찾을 수 없습니다."
        
    def generate_answer(self, query):
        if not self.gemini_model: return "AI 모델이 준비되지 않았습니다."
        # ... (기존 프롬프트 내용은 그대로 유지) ...
        prompt = f"""[당신의 역할] ... """
        try:
            response = self.gemini_model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Gemini API 호출 실패: {e}"); return "음... 답변을 생성하는 도중 문제가 발생했어요."

bot = PeopleAIBot()

# (신규) Gemini를 이용해 텍스트에서 책 정보를 추출하는 함수
def extract_book_info_from_text(text):
    if not bot.gemini_model:
        logger.error("Gemini 모델이 초기화되지 않았습니다."); return None
    try:
        prompt = f"""
        당신은 텍스트에서 도서 정보를 추출하는 전문가입니다.
        아래 텍스트에서 '책 제목', '저자', 'ISBN' 정보를 찾아서 JSON 형식으로만 응답해주세요.
        정보가 명확하지 않거나 없으면 빈 문자열("")을 값으로 넣어주세요.

        [분석할 텍스트]
        {text}

        [JSON 형식]
        {{
          "title": "추출한 책 제목",
          "author": "추출한 저자명",
          "isbn": "추출한 ISBN"
        }}
        """
        gemini_response = bot.gemini_model.generate_content(prompt)
        json_str_match = re.search(r'\{.*\}', gemini_response.text, re.DOTALL)
        if not json_str_match:
            logger.error(f"Gemini가 JSON 형식으로 응답하지 않았습니다: {gemini_response.text}"); return None
        
        book_data = json.loads(json_str_match.group(0))
        # 제목이나 저자 둘 중 하나라도 없으면 유효하지 않은 요청으로 판단
        if not book_data.get("title") and not book_data.get("author"):
             logger.warning(f"Gemini가 책 정보를 추출하지 못했습니다: {book_data}"); return None
        
        logger.info(f"Gemini를 통해 텍스트에서 책 정보 추출 성공: {book_data}")
        return book_data
    except Exception as e:
        logger.error(f"Gemini를 이용한 텍스트 분석 중 오류 발생: {e}"); return None


def add_book_to_sheet(book_info, user_name):
    if not gs_client: return False, "구글 시트 클라이언트 초기화 실패"
    try:
        sheet = gs_client.open_by_key(os.environ.get("GOOGLE_SHEET_ID")).sheet1
        new_row = [
            book_info.get('title'), book_info.get('author'), book_info.get('isbn', '정보 없음'),
            book_info.get('url', 'URL 없음'), user_name, datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ]
        sheet.append_row(new_row); return True, None
    except Exception as e:
        logger.error(f"구글 시트 데이터 추가 실패: {e}"); return False, str(e)

def handle_book_request(event, say):
    thread_ts = event.get("ts")
    user_id = event.get("user")
    text = event.get("text", "")
    
    processing_msg = say(text="✅ 도서 신청을 접수했습니다. 입력된 정보를 분석 중입니다...", thread_ts=thread_ts)
    
    book_info = extract_book_info_from_text(text)
    if not book_info:
        app.client.chat_update(channel=event.get("channel"), ts=processing_msg['ts'], text="⚠️ 메시지에서 책 제목, 저자 정보를 찾을 수 없습니다. 다시 한번 확인해주세요."); return
    
    try:
        user_name = app.client.users_info(user=user_id)["user"]["profile"].get("real_name", user_id)
    except Exception as e:
        logger.error(f"Slack 사용자 정보 조회 실패: {e}"); user_name = "알수없음"
    
    success, error_msg = add_book_to_sheet(book_info, user_name)
    reply_text = f"✅ 신청이 완료되었습니다.\n\n> *제목:* {book_info.get('title', 'N/A')}\n> *저자:* {book_info.get('author', 'N/A')}\n> *신청자:* {user_name}" if success else f"⚠️ 구글 시트에 기록 중 문제가 발생했습니다. (오류: {error_msg})"
    app.client.chat_update(channel=event.get("channel"), ts=processing_msg['ts'], text=reply_text)

def handle_thread_reply(event, say):
    clean_query = re.sub(f"<@{bot.bot_id}>", "", event.get("text", "")).strip()
    if not clean_query: return
    thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=event.get("thread_ts"))
    final_answer = bot.generate_answer(clean_query)
    app.client.chat_update(channel=event.get("channel"), ts=thinking_message['ts'], text=final_answer)

def handle_new_message(event, say):
    text = event.get("text", "").strip()
    if not text: return
    thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=event.get("ts"))
    final_answer = bot.generate_answer(text)
    app.client.chat_update(channel=event.get("channel"), ts=thinking_message['ts'], text=final_answer)

@app.event("message")
def handle_all_message_events(body, say, logger):
    try:
        event = body["event"]
        if "subtype" in event or (bot.bot_id and event.get("user") == bot.bot_id): return
        
        text = event.get("text", "")
        thread_ts = event.get("thread_ts")

        # 도서신청 키워드가 있고, 스레드가 아닌 경우에만 반응
        if not thread_ts and ("도서신청" in text or "도서 신청" in text):
            handle_book_request(event, say); return
        
        if thread_ts:
            if f"<@{bot.bot_id}>" in text:
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
    return "피플AI (Text-Parsing 최종) 정상 작동중! 🟢"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
