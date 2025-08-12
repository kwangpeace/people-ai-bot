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

# 구글 시트 연동을 위한 라이브러리
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

# --- 앱 초기화 ---
try:
    app = App(token=os.environ.get("SLACK_BOT_TOKEN"), signing_secret=os.environ.get("SLACK_SIGNING_SECRET"))
    flask_app = Flask(__name__)
    handler = SlackRequestHandler(app)
    logger.info("Slack App 및 Flask 앱 초기화 성공")
except Exception as e:
    logger.critical(f"앱 초기화 실패: {e}"); exit()

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
        self.setup_direct_answers()

    def setup_direct_answers(self):
        self.direct_answers = [
            {
                "keywords": ["외부 회의실", "외부회의실", "스파크플러스 예약", "4층 회의실"],
                "answer": """🔄 외부 회의실 예약 안내\n\n외부 회의실(스파크플러스) 예약이 필요하시면, 이 스레드에 **[날짜/시간, 예상 인원, 사용 목적]**을 모두 남겨주세요. 피플팀에서 예약 가능 여부를 확인한 후 답변 드리겠습니다. (담당: @김정수)"""
            }
        ]
        logger.info("특정 질문에 대한 직접 답변(치트키) 설정 완료.")

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
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            title_meta = soup.find("meta", property="og:title")
            author_meta = soup.find("meta", attrs={"name": "author"})
            isbn_meta = soup.find("meta", attrs={"name": "isbn"})

            title = title_meta["content"] if title_meta else None
            author = author_meta["content"] if author_meta else None
            isbn = isbn_meta["content"] if isbn_meta else None

            if not title:
                title_elem = soup.select_one('h1.prod_title, span.prod_title_text, h1.title')
                title = title_elem.get_text(strip=True) if title_elem else "제목을 찾을 수 없습니다."
            if not author:
                author_elem = soup.select_one('a.author, span.author')
                author = author_elem.get_text(strip=True) if author_elem else "저자를 찾을 수 없습니다."
            if not isbn:
                for tr in soup.select("div.prod_detail_area_bottom table tr"):
                    if th := tr.find("th", string=re.compile("ISBN")):
                        if td := tr.find("td"):
                            isbn = td.get_text(strip=True); break
                if not isbn or isbn == "ISBN 정보 없음":
                     isbn = "ISBN 정보 없음"

            logger.info(f"책 정보 추출 성공: 제목={title}, 저자={author}")
            return {"title": title, "author": author, "url": url, "isbn": isbn}
        except Exception as e:
            logger.error(f"도서 정보 추출 중 오류 발생: {e}"); return None
        
    def generate_answer(self, query):
        for item in self.direct_answers:
            if any(keyword in query for keyword in item["keywords"]):
                return item["answer"]
        if not self.gemini_model or not self.knowledge_base:
            return "AI 모델 또는 지식 베이스가 준비되지 않았습니다."
        
        # 사용자가 제공한 최신 프롬프트로 업데이트
        prompt = f"""
[당신의 역할]
당신은 '중고나라' 회사의 피플팀 AI 어시스턴트 '피플AI'입니다. 당신의 임무는 동료의 질문에 명확하고 간결하며, 가독성 높은 답변을 제공하는 것입니다.

[답변 생성 원칙]
1.  핵심 위주 답변: 사용자의 질문 의도를 파악하여 가장 핵심적인 답변을 간결하게 제공합니다.
2.  정보 출처 절대성: 모든 답변은 제공된 '[참고 자료]'에만 근거해야 합니다. 자료에 내용이 없으면 "음, 문의주신 부분에 대해서는 제가 지금 바로 명확한 답변을 드리기는 조금 어렵네요. 피플팀에서 확인 후 답변을 드리도록 하겠습니다." 와 같이 부드럽게 답변합니다.
3.  자연스러운 소통: "참고 자료에 따르면" 같은 표현 없이, 당신이 이미 알고 있는 지식처럼 자연스럽게 설명합니다.
4.  명확한 상황에 대한 인지: 질의와 응답은 슬랙 공개 채널인 '#문의-피플팀'에서 주로 대화를 나누는 상황입니다. 그래서 질문자, 피플AI봇이 대화하는 상황을 피플팀 담당자도 볼 수 있다는 점을 명심하세요.

[잘못된 답변 상황]
질문: 탕비실 싱크대에서 물이 새는거 같습니다 카페트가 젖어있어요!
답변: 탕비실 싱크대 누수 문제를 알려주셔서 감사합니다. :흰색_확인_표시: 탕비실 카페트가 젖어 있다면, 즉시 피플팀 @시현빈 매니저, @김정수 매니저에게 알려주시면 신속하게 조치하겠습니다.

[올바른 답변 상황]
질문: 탕비실 싱크대에서 물이 새는거 같습니다 카페트가 젖어있어요!
답변: 탕비실 싱크대 누수 문제를 알려주셔서 감사합니다. 즉시 피플팀에서 확인하고 조치하도록 하겠습니다.

[잘못된 답변 상황]
질문: 안녕하세요! 모니터가 계속 깜빡 거려서 혹시 케이블 교체 가능할 지 문의드립니다,
답변: 모니터 케이블 교체를 원하시는군요! :흰색_확인_표시: HDMI 선을 포함한 여분 케이블은 피플팀에서 관리하고 있습니다. :흰색_확인_표시: 피플팀 @시현빈 매니저에게 문의하시면 바로 확인 후 지원받으실 수 있습니다.

[올바른 답변 상황]
질문: 안녕하세요! 모니터가 계속 깜빡 거려서 혹시 케이블 교체 가능할 지 문의드립니다,
답변: 모니터 케이블 교체를 원하시는군요! HDMI 선을 포함한 여분 케이블은 피플팀에서 관리하고 있습니다. :흰색_확인_표시: 피플팀에서 확인 후 도움을 드릴 수 있도록 하겠습니다. :전구: 우선 HDMI 선을 새로 연결해보시고, 그래도 문제가 지속되면 모니터 자체의 문제일 수 있으니 다시 한번 확인부탁드립니다.

[답변 형식화 최종 규칙]
당신은 반드시 다음 규칙을 지켜 답변을 시각적으로 명확하고 부드럽게 구성해야 합니다.
- 구성: 복잡한 번호 매기기보다 간단한 소제목과 글머리 기호(-, ✅, 💡 등)를 사용하여 핵심적인 행동 위주로 안내합니다.
- 이모지: 🔄, ✅, 💡, ⚠️, 🔗 등 정보성 이모지를 사용하여 가독성을 높입니다. (감정, 전화 이모지 사용 금지)
- 마무리: 답변 마지막에 후속 질문을 유도하는 문구는 생략하여 대화를 간결하게 마무리합니다.
- 기본 규칙: 한 문장마다 줄바꿈하고, 굵은 글씨 등 텍스트 강조는 절대 사용하지 않습니다.

[좋은 답변 예시]
(예시 1: 문제 해결 안내)
모니터 연결에 문제가 있으시군요.
아래 사항들을 확인해보시겠어요?
[모니터 문제 해결]
✅ 모니터 전원 케이블과 PC 연결 케이블(HDMI 등)이 잘 꽂혀 있는지 확인합니다.
✅ (Mac 사용자) VPN(FortiClient)이나 Logitech 관련 프로그램이 실행 중이라면 종료한 후 다시 시도해보세요.
피플팀에서 확인 후 도움을 드리도록 하겠습니다.

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
            logger.error(f"Gemini API 호출 실패: {e}")
            return "음... 답변을 생성하는 도중 문제가 발생했어요. 잠시 후 다시 시도해보시겠어요? 😢"

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

def handle_new_message(event, say):
    channel_id = event.get("channel")
    text = event.get("text", "").strip()
    if not text or len(text) < 2: return
    clean_query = text.replace(f"<@{bot.bot_id}>", "").strip()
    message_ts = event.get("ts")
    thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=message_ts)
    final_answer = bot.generate_answer(clean_query)
    app.client.chat_update(channel=channel_id, ts=thinking_message['ts'], text=final_answer)

def handle_thread_reply(event, say):
    text = event.get("text", "")
    clean_query = text.replace(f"<@{bot.bot_id}>", "").strip()
    if not clean_query: return
    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts")
    thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=thread_ts)
    final_answer = bot.generate_answer(clean_query)
    app.client.chat_update(channel=channel_id, ts=thinking_message['ts'], text=final_answer)

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
            if "도움말" in text:
                say(text=bot.help_text, thread_ts=thread_ts if thread_ts else event.get("ts"))
            elif thread_ts:
                handle_thread_reply(event, say)
            else:
                handle_new_message(event, say)
    except Exception as e:
        logger.error(f"message 이벤트 처리 중 오류 발생: {e}", exc_info=True)

@flask_app.route("/slack/events", methods=["POST"])
def slack_events(): return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check(): return "피플AI (Google Sheets 최종) 정상 작동중! 🟢"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
