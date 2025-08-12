# 필요한 라이브러리들을 가져옵니다.
import os
import re
import json
import random
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime

import google.generativeai as genai
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

# --- 환경 변수 체크 ---
# 구글 관련 변수가 빠지고, N8N 웹훅 주소가 새로 추가되었습니다.
required_env = [
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "GEMINI_API_KEY",
    "N8N_BOOK_REQUEST_WEBHOOK" # n8n 연동을 위한 새 변수
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
        
        # GitHub 연동을 통해 지식/도움말 데이터를 가져옵니다.
        github_repo = "https://github.com/kwangpeace/people-ai-bot" # !본인 정보로 수정!
        self.knowledge_base = self.load_data_from_github(github_repo, "guide_data.txt")
        self.help_text = self.load_data_from_github(github_repo, "help.md", "도움말을 찾을 수 없습니다.")

        self.gemini_model = self.setup_gemini()
        self.responses = {"searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔"]}

    def load_data_from_github(self, repo, path, default_text=""):
        """GitHub Private 저장소에서 파일 내용을 읽어옵니다."""
        # 이 기능을 사용하려면 GITHUB_TOKEN 환경변수 설정이 필요합니다.
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            logger.error(f"'{path}' 로드를 위한 GITHUB_TOKEN 환경 변수가 없습니다.")
            return default_text
        
        url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
        headers = {"Authorization": f"token {token}"}
        
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                logger.info(f"GitHub에서 '{path}' 파일을 성공적으로 로드했습니다.")
                return response.text
            else:
                return default_text
        except Exception:
            return default_text

    def extract_book_info(self, url):
        """URL에서 책 정보를 추출합니다. (웹 스크래핑)"""
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

    def setup_gemini(self):
        """Gemini AI 모델을 설정합니다."""
        try:
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            model = genai.GenerativeModel("gemini-1.5-flash")
            logger.info("Gemini API 활성화 완료.")
            return model
        except Exception as e:
            logger.error(f"Gemini 모델 설정 실패: {e}")
            return None

    def generate_answer(self, query):
        """사용자의 질문에 대해 AI 답변을 생성합니다."""
        if not self.gemini_model: return "AI 모델이 설정되지 않아 답변할 수 없습니다."
        if not self.knowledge_base: return "참고할 지식 데이터가 없어 답변할 수 없습니다."
        
        prompt = f"""
        당신은 '중고나라'의 HR 어시스턴트 '피플AI봇'입니다. 제공된 참고자료를 바탕으로, 동료의 질문에 명확하고 친절하게 답변해주세요.
        ---
        [참고 자료]
        {self.knowledge_base}
        ---
        [질문]
        {query}
        ---
        [답변]
        """
        try:
            response = self.gemini_model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Gemini API 호출 실패: {e}", exc_info=True)
            return "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 😢"

# 봇 인스턴스 생성
bot = PeopleAIBot()

# --- 새롭게 추가된 n8n 호출 함수 ---
def trigger_n8n_book_request(book_info, user_name):
    """n8n 도서신청 워크플로우를 호출(트리거)하는 함수"""
    webhook_url = os.environ.get("N8N_BOOK_REQUEST_WEBHOOK")
    
    try:
        # n8n으로 보낼 데이터 묶음(payload)을 구성합니다.
        payload = {
            "title": book_info['title'],
            "author": book_info['author'],
            "url": book_info['url'],
            "user_name": user_name,
            "request_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status() # HTTP 에러 발생 시 예외 처리
        
        logger.info("n8n 워크플로우를 성공적으로 호출했습니다.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"n8n 워크플로우 호출 실패: {e}")
        return False

# --- 슬랙 이벤트 핸들러 ---
@app.event("message")
def handle_message_events(body, say):
    """모든 메시지 이벤트를 수신하고 적절히 처리합니다."""
    try:
        event = body["event"]
        if "subtype" in event or (bot.bot_id and event.get("user") == bot.bot_id):
            return

        text = event.get("text", "").strip()
        user_id = event.get("user")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts", event.get("ts"))

        if f"<@{bot.bot_id}>" in text:
            clean_query = text.replace(f"<@{bot.bot_id}>", "").strip()

            # "도서신청" 명령어 우선 처리
            if clean_query.startswith("도서신청"):
                url_match = re.search(r"https?://\S+", clean_query)
                if not url_match:
                    say(text="⚠️ 도서신청 명령어와 함께 교보문고 URL을 입력해주세요.", thread_ts=thread_ts)
                    return
                
                url = url_match.group(0)
                processing_msg = say(text=f"✅ 도서 신청을 접수했습니다. n8n 워크플로우에 전달할게요...", thread_ts=thread_ts)
                
                book_info = bot.extract_book_info(url)
                if book_info and book_info["title"] != "제목을 찾을 수 없습니다.":
                    user_info = app.client.users_info(user=user_id)
                    user_name = user_info["user"]["profile"].get("real_name", user_id)
                    
                    # 구글 시트 함수 대신 n8n 호출 함수를 실행합니다.
                    success = trigger_n8n_book_request(book_info, user_name)
                    
                    if success:
                        reply_text = "✅ n8n에 도서 신청을 안전하게 전달했습니다! 잠시 후 구글 시트를 확인해주세요."
                    else:
                        reply_text = "⚠️ n8n 워크플로우를 호출하는 중 문제가 발생했습니다. 피플팀에 문의해주세요."
                else:
                    reply_text = "⚠️ 해당 링크에서 도서 정보를 찾을 수 없습니다. 교보문고 상품 상세 링크가 맞는지 확인해주세요."
                
                app.client.chat_update(channel=channel_id, ts=processing_msg['ts'], text=reply_text)
                return

            # "도움말" 명령어 처리
            if clean_query == "도움말":
                say(text=bot.help_text, thread_ts=thread_ts)
                return

            # 그 외 모든 멘션은 AI 답변으로 처리
            if clean_query:
                thinking_msg = say(text=random.choice(bot.responses['searching']), thread_ts=thread_ts)
                final_answer = bot.generate_answer(clean_query)
                app.client.chat_update(channel=channel_id, ts=thinking_msg['ts'], text=final_answer)

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
