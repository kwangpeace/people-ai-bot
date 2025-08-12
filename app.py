# -*- coding: utf-8 -*-
# 필요한 라이브러리들을 가져옵니다.
import os
import random
import logging
import re
import json
from datetime import datetime
import asyncio

# 웹 관련 라이브러리
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# AI 및 슬랙, 구글 시트 관련 라이브러리
import google.generativeai as genai
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_bolt.async_app import AsyncApp
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

# --- 앱 초기화 (비동기 방식으로 변경) ---
try:
    app = AsyncApp(token=os.environ.get("SLACK_BOT_TOKEN"), signing_secret=os.environ.get("SLACK_SIGNING_SECRET"))
    flask_app = Flask(__name__)
    handler = SlackRequestHandler(app)
    logger.info("Async Slack App 및 Flask 앱 초기화 성공")
except Exception as e:
    logger.critical(f"앱 초기화 실패: {e}"); exit()

# --- 메인 봇 클래스 ---
class PeopleAIBot:
    def __init__(self):
        self.bot_id = None
        self.gemini_model = self.setup_gemini()
        self.knowledge_base = self.load_knowledge_file()
        self.help_text = self.load_help_file()
        self.responses = { "searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔"] }
        self.setup_direct_answers()

    async def initialize_bot_id(self):
        try:
            auth_test_response = await app.client.auth_test()
            self.bot_id = auth_test_response['user_id']
            logger.info(f"봇 ID({self.bot_id})를 성공적으로 가져왔습니다.")
        except Exception as e:
            logger.error(f"봇 ID 가져오기 실패: {e}")

    def setup_direct_answers(self):
        self.direct_answers = [
            {
                "keywords": ["외부 회의실", "외부회의실", "스파크플러스 예약"],
                "answer": """🔄 외부 회의실 예약 안내\n\n외부 회의실(스파크플러스) 예약이 필요하시면, 이 스레드에 **[날짜/시간, 예상 인원, 사용 목적]**을 모두 남겨주세요. 피플팀에서 예약 가능 여부를 확인한 후 답변 드리겠습니다."""
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
        except FileNotFoundError: return ""

    def load_help_file(self):
        try:
            with open("help.md", 'r', encoding='utf-8') as f: return f.read()
        except FileNotFoundError: return "도움말 파일을 찾을 수 없습니다."

    async def extract_book_info(self, url):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.goto(url, timeout=20000)
                html_content = await page.content()
                await browser.close()

            if not self.gemini_model:
                logger.error("Gemini 모델이 초기화되지 않았습니다."); return None

            prompt = f"""
            당신은 웹사이트 HTML 코드 분석 전문가입니다. 아래 HTML 코드에서 '책 제목', '저자', 'ISBN' 정보를 찾아 JSON 형식으로만 응답해주세요.

            [HTML 코드]
            {html_content[:8000]} 

            [JSON 형식]
            {{ "title": "추출한 책 제목", "author": "추출한 저자명", "isbn": "추출한 ISBN" }}
            """
            gemini_response = self.gemini_model.generate_content(prompt)
            json_str_match = re.search(r'\{.*\}', gemini_response.text, re.DOTALL)
            
            if not json_str_match:
                logger.error(f"Gemini가 JSON 형식으로 응답하지 않았습니다: {gemini_response.text}"); return None
            
            book_data = json.loads(json_str_match.group(0))
            if not book_data.get("title"):
                 logger.warning(f"Gemini가 책 정보를 추출하지 못했습니다: {book_data}"); return None
            
            logger.info(f"Playwright와 Gemini를 통해 책 정보 추출 성공: {book_data}")
            return {"title": book_data.get("title"), "author": book_data.get("author"), "url": url, "isbn": book_data.get("isbn", "정보 없음")}
        except Exception as e:
            logger.error(f"Playwright를 이용한 도서 정보 추출 중 오류 발생: {e}"); return None
        
    def generate_answer(self, query):
        for item in self.direct_answers:
            if any(keyword in query for keyword in item["keywords"]):
                return item["answer"]
        if not self.gemini_model or not self.knowledge_base:
            return "AI 모델 또는 지식 베이스가 준비되지 않았습니다."
        
        prompt = f"""
[당신의 역할]
당신은 '중고나라' 회사의 피플팀 AI 어시스턴트 '피플AI'입니다. 당신의 임무는 동료의 질문에 명확하고 간결하며, 가독성 높은 답변을 제공하는 것입니다.

[답변 생성 원칙]
1.  **핵심 위주 답변**: 사용자의 질문 의도를 파악하여 가장 핵심적인 답변을 간결하게 제공합니다.
2.  **정보 출처 절대성**: 모든 답변은 제공된 '[참고 자료]'에만 근거해야 합니다.
3.  **자연스러운 소통**: "참고 자료에 따르면" 같은 표현 없이, 당신이 이미 알고 있는 지식처럼 자연스럽게 설명합니다.
4.  **팀으로서의 응답 (가장 중요!):** 당신은 피플팀의 공식적인 첫 번째 창구입니다. 따라서, 절대 특정 담당자 개인에게 문의하라고 안내하지 않습니다. 대신 "피플팀에서 확인 후 도움을 드리겠습니다" 또는 "피플팀에서 확인하고 조치하겠습니다" 와 같이, 팀 전체가 인지하고 대응하는 듯한 뉘앙스로 답변해야 합니다.
5.  **모르는 질문 처리:** 참고 자료에 내용이 없어 답변이 불가능할 경우, "음, 문의주신 부분에 대해서는 제가 지금 바로 명확한 답변을 드리기는 조금 어렵네요. 피플팀에서 확인 후 답변을 드리도록 하겠습니다." 와 같이 부드럽게 답변하며 팀에게 문의를 넘깁니다.

[답변 형식화 최종 규칙]
- **구성**: 복잡한 번호 매기기보다 간단한 소제목과 글머리 기호(-, ✅, 💡 등)를 사용하여 핵심적인 행동 위주로 안내합니다.
- **이모지**: 🔄, ✅, 💡, ⚠️, 🔗 등 정보성 이모지를 사용하여 가독성을 높입니다. (감정, 전화 이모지 사용 금지)
- **마무리**: 답변 마지막에 후속 질문을 유도하는 문구는 생략하여 대화를 간결하게 마무리합니다.
- **기본 규칙**: 한 문장마다 줄바꿈하고, 굵은 글씨 등 텍스트 강조는 절대 사용하지 않습니다.

[좋은 답변 예시]
(예시 1: 문제 해결 안내)
모니터 연결에 문제가 있으시군요.
아래 사항들을 확인해보시겠어요?
[모니터 문제 해결]
✅ 모니터 전원 케이블과 PC 연결 케이블(HDMI 등)이 잘 꽂혀 있는지 확인합니다.
✅ (Mac 사용자) VPN(FortiClient)이나 Logitech 관련 프로그램이 실행 중이라면 종료한 후 다시 시도해보세요.
그래도 해결되지 않으면, 피플팀에서 확인 후 도움을 드리도록 하겠습니다.

(예시 2: 절차 안내)
📦 중고나라 택배 발송 안내
중고나라는 임직원의 중고거래 활동을 지원하기 위해 개인 택배 발송 업무를 지원하고 있습니다.
🚚 [택배 발송 절차]
1. 물품 포장: 탕비실에 비치된 포장 물품을 이용하여 안전하게 직접 포장해주세요.
2. 송장 출력: 탕비실 내 송장 출력용 PC에서 택배사 웹 프로그램을 통해 송장을 직접 출력합니다.
3. 송장 부착: 박스 정면의 적절한 위치에 송장을 깔끔하게 부착해주세요.
4. 물품 배출: 송장이 부착된 박스를 4층 엘리베이터 옆 '중고나라 전용 택배함'에 넣어주세요.

(예시 3: 시스템 사용법 안내)
안녕하세요!
사내 복합기 및 팩스 사용 방법을 안내해 드릴게요.
🔄 복합기 설정 절차
1. 복합기 계정을 등록해주세요.
   - 🔗 계정 등록 링크: https://cloudmps.sindoh.com:8443/sparkplus/loginForm?clientLanguage=ko
2. 필수 프로그램을 설치해주세요.
   - 🔗 프로그램 설치 링크: https://cloudmps.sindoh.com:8443/sparkplus/loginForm?clientLanguage=ko
3. 인증카드를 등록해주세요.
   - 🔗 상세 가이드: https://sparkplus.oopy.io/373bbaf2-d7b0-4621-9e39-5aa630ba0757
💡 자주 묻는 질문 (FAQ)
- 인증카드: NFC 기능이 있는 스마트폰이나 교통카드 기능이 포함된 신용/체크카드를 사용할 수 있습니다.
- 카드 재등록: 기기 변경이나 분실 시, 별도 해지 절차 없이 새로 등록하면 됩니다.
- Mac 출력 오류: VPN(FortiClient) 또는 Logitech 관련 프로그램과 IP 충돌이 원인일 수 있습니다. 해당 프로그램을 종료한 후 다시 시도해보세요.

(예시 4: 정보 안내 - 와이파이)
안녕하세요!
사내 와이파이 정보를 안내해 드릴게요.
🏢 직원용 Wi-Fi
- SSID: joonggonara-2G / joonggonara-5G
- 비밀번호: jn2023!@
👥 방문객용 Wi-Fi
- SSID: joonggonara-guest-2G / joonggonara-guest-5G
- 비밀번호: guest2023!@

(예시 5: 시설 이용 안내 - 주차)
방문객 주차 등록 방법을 안내해 드립니다.
🔄 주차 등록 절차
1. 하이파킹 웹/앱에 접속합니다.
2. 방문 차량의 전체 번호를 입력하여 조회합니다.
3. 적용할 할인권을 선택합니다.
4. 내부 규정에 따라 정산 대장을 작성합니다.
👥 지원 대상
- 공식적인 미팅 등 업무 목적으로 방문한 외부 고객
- 직원 개인 차량은 원칙적으로 지원되지 않습니다. (단, 업무 목적 시 피플팀 사전 승인 후 가능)
🔐 하이파킹 시스템 정보
- ID: petax@joonggonara.co.kr
- PW: jn2023!@

(예시 6: 외부회의실 예약)
안녕하세요!
외부 회의실(스파크플러스) 예약 방법을 안내해 드릴게요.
🔄 예약 절차
1. 외부 회의실 예약 요청 시 아래 세 가지 정보를 꼭 알려주셔야 합니다.
   ✅ 사용 목적
   📅 날짜 및 시간
   👥 참석 인원 수
2. 피플팀에서 예약 가능 여부를 확인한 후, 이 스레드로 답변을 드릴게요.

(예시 7: 제도 안내 - 자격증 취득 지원)
자격증 취득 지원 제도에 대해 안내해 드릴게요.
👥 지원 대상: 중고나라 본사 정규직 직원
💰 지원 금액: 1인당 1회 최대 20만원 (응시료 실비)
⚠️ 참고: 교재비, 학원비는 지원에서 제외됩니다.
🔄 진행 절차
1. 사전 신청: 플렉스에서 '자격증 도전 신청서'를 작성하여 제출합니다.
2. 사후 정산: 합격 후 '자격증 취득 지원금 신청서'를 제출합니다.
3. 지급: 승인 후 다음 달 급여에 합산되어 지급됩니다.

(예시 8: 제도 안내 - 지식공유회)
사내 지식공유회에 대해 안내해 드립니다.
👥 참여 대상: 누구나 강연자 또는 참석자로 자유롭게 참여할 수 있습니다.
💰 강사료 지원: 사내 강사에게는 시간당 50,000원의 강사료가 지급됩니다.
📝 참여 방법
- 강연자: 신청 양식 작성 후 피플팀에 알려주세요.
- 참석자: 사내에 공지된 세션 일정을 확인하고, 안내에 따라 참석 신청을 합니다.

(예시 9: 절차 안내 - 온라인 교육 신청)
온라인 교육 신청 방법을 안내해 드릴게요.
🔄 신청 절차
1. 온라인 교육 신청서를 작성하여 제출합니다. (링크는 관련 공지 확인)
2. 신청서 제출 전 아래 사항을 확인해주세요.
   ⚠️ 30만원 이상 고가 교육은 반드시 사전 품의를 먼저 받아야 합니다.
   ✅ 회사에 이미 있는 교육 과정인지 중복 확인이 필요합니다.
3. 피플팀에서 매주 금요일 신청 건을 취합하여 일괄 결제를 진행합니다.

(예시 10: 절차 안내 - 오프라인 교육 신청)
오프라인 교육 신청 방법을 안내해 드립니다.
💰 유료 교육
- 신청: 플렉스에서 '교육 참가 신청서'를 작성하여 제출합니다.
- ⚠️ 30만원 이상 고가 교육은 반드시 사전 품의가 필요합니다.
- 결제: 피플팀에서 매주 금요일 일괄 결제를 진행합니다.
✅ 무료 교육
- 별도 신청서는 필요 없으나, 업무 활동으로 기록하기 위해 플렉스에서 '외근 신청서(비용 미발생 건)'를 등록하고 승인받아야 합니다.
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
            logger.error(f"Gemini API 호출 실패: {e}"); return "음... 답변을 생성하는 도중 문제가 발생했어요. 잠시 후 다시 시도해보시겠어요? 😢"

bot = PeopleAIBot()

def add_book_to_sheet(book_info, user_name):
    if not gs_client: return False, "구글 시트 클라이언트 초기화 실패"
    try:
        sheet = gs_client.open_by_key(os.environ.get("GOOGLE_SHEET_ID")).sheet1
        new_row = [book_info.get('title'), book_info.get('author'), book_info.get('isbn'), book_info.get('url'), user_name, datetime.now().strftime('%Y-%m-%d %H:%M:%S')]
        sheet.append_row(new_row)
        logger.info(f"구글 시트에 새 도서 추가 성공: {book_info.get('title')}"); return True, None
    except Exception as e:
        logger.error(f"구글 시트 데이터 추가 실패: {e}"); return False, str(e)

async def handle_book_request(event, say):
    thread_ts = event.get("ts")
    user_id = event.get("user")
    url = re.search(r"https?://\S+", event.get("text", "")).group(0)
    
    processing_msg_resp = await say(text="✅ 도서 신청을 접수했습니다. 잠시만 기다려주세요...", thread_ts=thread_ts)
    processing_msg_ts = processing_msg_resp['ts']
    
    book_info = await bot.extract_book_info(url)
    if not book_info:
        await app.client.chat_update(channel=event.get("channel"), ts=processing_msg_ts, text="⚠️ 해당 링크에서 책 정보를 찾을 수 없습니다. 링크를 다시 확인해주세요."); return
    
    try:
        user_info_resp = await app.client.users_info(user=user_id)
        user_name = user_info_resp["user"]["profile"].get("real_name", user_id)
    except Exception as e:
        logger.error(f"Slack 사용자 정보 조회 실패: {e}"); user_name = "알수없음"
    
    success, error_msg = add_book_to_sheet(book_info, user_name)
    reply_text = f"✅ 신청이 완료되었습니다.\n\n> *제목:* {book_info['title']}\n> *신청자:* {user_name}" if success else f"⚠️ 구글 시트에 기록 중 문제가 발생했습니다. (오류: {error_msg})"
    await app.client.chat_update(channel=event.get("channel"), ts=processing_msg_ts, text=reply_text)

async def handle_thread_reply(event, say):
    clean_query = re.sub(f"<@{bot.bot_id}>", "", event.get("text", "")).strip()
    if not clean_query: return
    thinking_message_resp = await say(text=random.choice(bot.responses['searching']), thread_ts=event.get("thread_ts"))
    thinking_message_ts = thinking_message_resp['ts']
    final_answer = bot.generate_answer(clean_query)
    await app.client.chat_update(channel=event.get("channel"), ts=thinking_message_ts, text=final_answer)

async def handle_new_message(event, say):
    text = event.get("text", "").strip()
    if not text: return
    message_ts = event.get("ts")
    thinking_message_resp = await say(text=random.choice(bot.responses['searching']), thread_ts=message_ts)
    thinking_message_ts = thinking_message_resp['ts']
    final_answer = bot.generate_answer(text)
    await app.client.chat_update(channel=event.get("channel"), ts=thinking_message_ts, text=final_answer)

@app.event("message")
async def handle_all_message_events(body, say, logger):
    try:
        event = body["event"]
        if "subtype" in event or (bot.bot_id and event.get("user") == bot.bot_id): return
        text = event.get("text", "")
        thread_ts = event.get("thread_ts")

        if not thread_ts and re.search(r"https?://\S+", text) and ("도서신청" in text or "도서 신청" in text):
            await handle_book_request(event, say); return
        
        if thread_ts:
            if f"<@{bot.bot_id}>" in text:
                await handle_thread_reply(event, say)
        else:
            if "도움말" in text.strip().lower():
                await say(text=bot.help_text, thread_ts=event.get("ts"))
            else:
                await handle_new_message(event, say)
    except Exception as e:
        logger.error(f"message 이벤트 처리 중 오류 발생: {e}", exc_info=True)

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check():
    return "피플AI (Gemini-Scraper 최종) 정상 작동중! 🟢"

async def main():
    await bot.initialize_bot_id()
    # In a server environment, the app is run by a WSGI server like Gunicorn
    # For local development, you might run it like this:
    # from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    # handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    # await handler.start_async()

if __name__ == "__main__":
    # This block is mainly for local testing. Gunicorn runs the 'flask_app'.
    port = int(os.environ.get("PORT", 3000))
    # It's better to initialize bot_id once before running
    asyncio.run(main())
    flask_app.run(host="0.0.0.0", port=port)
