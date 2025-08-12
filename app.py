# -*- coding: utf-8 -*-
import os
import random
import logging
import re
import json
from datetime import datetime
import asyncio

# Google Search API library
from googleapiclient.discovery import build

# AI, Slack, and Google Sheets libraries
import google.generativeai as genai
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
import gspread
from google.oauth2.service_account import Credentials

# --- Environment Variable Check ---
required_env = [
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "GEMINI_API_KEY",
    "GOOGLE_CREDENTIALS_JSON",
    "GOOGLE_SHEET_ID",
    "GOOGLE_SHEET_NAME",
    "GOOGLE_API_KEY",      # For Google Search
    "SEARCH_ENGINE_ID"     # For Google Search
]
for key in required_env:
    if not os.environ.get(key):
        logging.critical(f"환경 변수 '{key}'가 설정되지 않았습니다. 앱을 시작할 수 없습니다.")
        exit()

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

# --- Google Clients Initialization ---
def setup_gspread_client():
    try:
        creds_json_str = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        creds_info = json.loads(creds_json_str)
        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)
        logger.info("Google Sheets client initialized successfully.")
        return client
    except Exception as e:
        logger.critical(f"Failed to initialize Google Sheets client: {e}"); return None

gs_client = setup_gspread_client()

# --- App Initialization ---
app = App(token=os.environ.get("SLACK_BOT_TOKEN"), signing_secret=os.environ.get("SLACK_SIGNING_SECRET"))
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

# --- Main Bot Class ---
class PeopleAIBot:
    def __init__(self):
        try:
            self.bot_id = app.client.auth_test()['user_id']
            logger.info(f"Bot ID({self.bot_id}) successfully fetched.")
        except Exception as e:
            logger.error(f"Failed to fetch Bot ID: {e}"); self.bot_id = None
        
        self.gemini_model = self.setup_gemini()
        self.knowledge_base = self.load_knowledge_file()
        self.help_text = self.load_help_file()
        self.responses = { "searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔"] }

    def setup_gemini(self):
        try:
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            return genai.GenerativeModel("gemini-1.5-flash")
        except Exception as e:
            logger.error(f"Failed to set up Gemini model: {e}"); return None

    def load_knowledge_file(self):
        try:
            with open("guide_data.txt", 'r', encoding='utf-8') as f: return f.read()
        except FileNotFoundError: return ""

    def load_help_file(self):
        try:
            with open("help.md", 'r', encoding='utf-8') as f: return f.read()
        except FileNotFoundError: return "Help file not found."
        
    def generate_answer(self, query):
        if not self.gemini_model: return "AI model is not ready."
        prompt = f"""
[당신의 역할]
... (Your full, detailed prompt here) ...
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
            return response.text
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}"); return "An error occurred while generating a response."

bot = PeopleAIBot()

# --- Helper Functions for Book Search ---

def search_google_for_book(query):
    try:
        service = build("customsearch", "v1", developerKey=os.environ.get("GOOGLE_API_KEY"))
        res = service.cse().list(q=query, cx=os.environ.get("SEARCH_ENGINE_ID"), num=3).execute()
        
        search_results = []
        for item in res.get('items', []):
            result = {
                "title": item.get('title'),
                "link": item.get('link'),
                "snippet": item.get('snippet')
            }
            search_results.append(result)
        logger.info(f"Google search for '{query}' returned {len(search_results)} results.")
        return search_results
    except Exception as e:
        logger.error(f"Google search failed: {e}")
        return None

def get_book_details_from_search(user_text):
    if not bot.gemini_model: return None
    try:
        # Step 1: Extract the book title from the user's text
        extract_prompt = f"다음 문장에서 책 제목만 추출해줘: \"{user_text}\""
        title_response = bot.gemini_model.generate_content(extract_prompt)
        book_title = title_response.text.strip().replace('"', '').replace("'", "")
        
        if not book_title: 
            logger.warning("Failed to extract book title from text."); return None

        # Step 2: Search for the extracted title
        search_results = search_google_for_book(book_title)
        if not search_results:
            logger.warning("No search results found for the book title."); return None

        # Step 3: Use Gemini to synthesize the search results
        synthesis_prompt = f"""
        사용자가 '{book_title}' 책을 신청했습니다. 아래는 구글 검색 결과입니다.
        검색 결과를 바탕으로 이 책의 '제목', '저자', '출판사', '100자 내외 주요 내용'을 추출해서 JSON 형식으로만 응답해주세요.
        
        [검색 결과]
        {json.dumps(search_results, indent=2, ensure_ascii=False)}

        [JSON 형식]
        {{
            "title": "정확한 책 제목",
            "author": "저자명",
            "publisher": "출판사명",
            "summary": "100자 내외의 주요 내용 요약"
        }}
        """
        final_response = bot.gemini_model.generate_content(synthesis_prompt)
        json_str_match = re.search(r'\{.*\}', final_response.text, re.DOTALL)
        if not json_str_match:
            logger.error(f"Gemini failed to synthesize search results into JSON: {final_response.text}"); return None

        book_details = json.loads(json_str_match.group(0))
        logger.info(f"Successfully synthesized book details: {book_details}")
        return book_details

    except Exception as e:
        logger.error(f"Error in book detail extraction process: {e}"); return None

def add_book_to_sheet(book_info, user_name):
    if not gs_client: return False, "Google Sheets client not initialized"
    try:
        workbook = gs_client.open_by_key(os.environ.get("GOOGLE_SHEET_ID"))
        sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "Sheet1")
        sheet = workbook.worksheet(sheet_name)
        
        new_row = [
            book_info.get('title'), book_info.get('author'), book_info.get('publisher', 'N/A'),
            book_info.get('summary', 'N/A'), user_name, datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ]
        sheet.append_row(new_row)
        logger.info(f"Successfully added new book to sheet '{sheet_name}': {book_info.get('title')}"); return True, None
    except Exception as e:
        logger.error(f"Failed to add data to Google Sheet: {e}"); return False, str(e)

# --- Slack Event Handlers ---

def handle_book_request(event, say):
    thread_ts = event.get("ts")
    user_id = event.get("user")
    text = event.get("text", "")
    
    processing_msg = say(text="✅ 도서 신청을 접수했습니다. 책 정보를 검색하고 정리하는 중입니다...", thread_ts=thread_ts)
    
    book_info = get_book_details_from_search(text)
    
    if not book_info:
        app.client.chat_update(channel=event.get("channel"), ts=processing_msg['ts'], text="⚠️ 책 정보를 찾는 데 실패했습니다. 책 제목을 좀 더 명확하게 알려주시겠어요?"); return
    
    try:
        user_name = app.client.users_info(user=user_id)["user"]["profile"].get("real_name", user_id)
    except Exception as e:
        logger.error(f"Failed to get Slack user info: {e}"); user_name = "Unknown"
    
    success, error_msg = add_book_to_sheet(book_info, user_name)
    
    if success:
        reply_text = f"""✅ 신청이 완료되었습니다. 아래 정보로 구글 시트에 기록했습니다.
> *제목:* {book_info.get('title', 'N/A')}
> *저자:* {book_info.get('author', 'N/A')}
> *출판사:* {book_info.get('publisher', 'N/A')}
> *주요 내용:* {book_info.get('summary', 'N/A')}
> *신청자:* {user_name}"""
    else:
        reply_text = f"⚠️ 구글 시트에 기록 중 문제가 발생했습니다. (오류: {error_msg})"
        
    app.client.chat_update(channel=event.get("channel"), ts=processing_msg['ts'], text=reply_text)

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

        if not thread_ts and ("도서신청" in text or "도서 신청" in text):
            handle_book_request(event, say); return
        
        if not thread_ts: # Respond to all new messages
            handle_new_message(event, say)
        elif f"<@{bot.bot_id}>" in text: # Respond in threads only if mentioned
            # A simple thread handler can just call the new message handler
            handle_new_message(event, say)

    except Exception as e:
        logger.error(f"Error in message event handler: {e}", exc_info=True)

# --- Flask Routes ---
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check():
    return "PeopleAI Bot (Search-Enabled) is running! 🟢"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
