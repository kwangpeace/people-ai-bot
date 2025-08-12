# -*- coding: utf-8 -*-
# 필요한 라이브러리들을 가져옵니다.
import os
import random
import logging
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

import google.generativeai as genai
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

# 구글 시트 연동 라이브러리
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
        logger.critical(f"구글 시트 클라이언트 초기화 실패: {e}")
        return None

gs_client = setup_gspread_client()

# --- 앱 초기화 ---
try:
    app = App(token=os.environ.get("SLACK_BOT_TOKEN"), signing_secret=os.environ.get("SLACK_SIGNING_SECRET"))
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
        except FileNotFoundError:
            logger.error("'guide_data.txt' 파일을 찾을 수 없습니다."); return ""

    def load_help_file(self):
        try:
            with open("help.md", 'r', encoding='utf-8') as f: return f.read()
        except FileNotFoundError:
            logger.error("'help.md' 파일을 찾을 수 없습니다."); return "도움말 파일을 찾을 수 없습니다."

    def extract_book_info(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            title = soup.select_one('h1.prod_title, span.prod_title_text, h1.title, meta[property="og:title"]')
            title_text = title.get('content', title.get_text(strip=True)) if title else "제목을 찾을 수 없습니다."

            author = soup.select_one('a.author, span.author, meta[name="author"]')
            author_text = author.get('content', author.get_text(strip=True)) if author else "저자를 찾을 수 없습니다."

            isbn = "ISBN 정보 없음"
            for tr in soup.select("div.prod_detail_area_bottom table tr"):
                if th := tr.find("th", string=re.compile("ISBN")):
                    if td := tr.find("td"):
                        isbn = td.get_text(strip=True); break

            return {"title": title_text, "author": author_text, "url": url, "isbn": isbn}
        except Exception as e:
            logger.error(f"도서 정보 추출 중 오류 발생: {e}"); return None

    def generate_answer(self, query):
        if not self.gemini_model or not self.knowledge_base: return "AI 모델 또는 지식 베이스가 준비되지 않았습니다."
        prompt = f"""[당신의 역할]... (생략 없는 전체 프롬프트 내용을 여기에 붙여넣어주세요) ..."""
        try:
            response = self.gemini_model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Gemini API 호출 실패: {e}"); return "답변 생성 중 문제가 발생했습니다."

bot = PeopleAIBot()

def add_book_to_sheet(book_info, user_name):
    if not gs_client:
        logger.error("구글 시트 클라이언트가 초기화되지 않아 작업을 중단합니다.")
        return False, "구글 시트 클라이언트 초기화 실패"
    try:
        sheet = gs_client.open_by_key(os.environ.get("GOOGLE_SHEET_ID")).sheet1
        new_row = [
            book_info.get('title'), book_info.get('author'), book_info.get('isbn'),
            book_info.get('url'), user_name, datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ]
        sheet.append_row(new_row)
        logger.info(f"구글 시트에 새 도서 추가 성공: {book_info.get('title')}")
        return True, None
    except Exception as e:
        logger.error(f"구글 시트 데이터 추가 실패: {e}"); return False, str(e)

def handle_book_request(event, say):
    thread_ts = event.get("ts")
    user_id = event.get("user")
    text = event.get("text", "")
    url_match = re.search(r"https?://\S+", text)
    url = url_match.group(0)

    processing_msg = say(text="✅ 도서 신청을 접수했습니다. 잠시만 기다려주세요...", thread_ts=thread_ts)
    book_info = bot.extract_book_info(url)

    if not book_info or book_info["title"] == "제목을 찾을 수 없습니다.":
        reply_text = "⚠️ 해당 링크에서 책 정보를 찾을 수 없습니다. 링크를 다시 확인해주세요."
        app.client.chat_update(channel=event.get("channel"), ts=processing_msg['ts'], text=reply_text)
        return

    user_name = "알수없음"
    try:
        user_info = app.client.users_info(user=user_id)
        user_name = user_info["user"]["profile"].get("real_name", user_id)
    except Exception as e:
        logger.error(f"Slack 사용자 정보 조회 실패: {e}")

    success, error_msg = add_book_to_sheet(book_info, user_name)
    if success:
        reply_text = f"✅ 신청이 완료되어 구글 시트에 기록되었습니다.\n\n> *제목:* {book_info['title']}\n> *저자:* {book_info['author']}\n> *신청자:* {user_name}"
    else:
        reply_text = f"⚠️ 구글 시트에 기록 중 문제가 발생했습니다. (오류: {error_msg})"

    app.client.chat_update(channel=event.get("channel"), ts=processing_msg['ts'], text=reply_text)

def handle_thread_reply(event, say):
    clean_query = re.sub(f"<@{bot.bot_id}>", "", event.get("text", "")).strip()
    if not clean_query: return
    thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=event.get("thread_ts"))
    final_answer = bot.generate_answer(clean_query)
    app.client.chat_update(channel=event.get("channel"), ts=thinking_message['ts'], text=final_answer)

@app.event("message")
def handle_all_message_events(body, say, logger):
    try:
        event = body["event"]
        if "subtype" in event or (bot.bot_id and event.get("user") == bot.bot_id): return

        text = event.get("text", "")
        thread_ts = event.get("thread_ts")

        if not thread_ts and re.search(r"https?://\S+", text) and ("도서신청" in text or "도서 신청" in text):
            logger.info(f"도서신청 키워드 및 URL 감지: {text[:50]}...")
            handle_book_request(event, say)
            return

        if f"<@{bot.bot_id}>" in text:
            if thread_ts:
                handle_thread_reply(event, say)
            elif "도움말" in text:
                say(text=bot.help_text, thread_ts=event.get("ts"))

    except Exception as e:
        logger.error(f"message 이벤트 처리 중 오류 발생: {e}", exc_info=True)

@flask_app.route("/slack/events", methods=["POST"])
def slack_events(): return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check(): return "피플AI (Google Sheets 연동 최종) 정상 작동중! 🟢"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
