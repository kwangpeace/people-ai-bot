import os
import random
import logging
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
import google.generativeai as genai

# --- 로깅 설정 ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)
logger.info("<<<<< 최종 버전 코드 실행 확인 >>>>>") 

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
    logger.critical(f"앱 초기화 실패! 환경 변수를 확인하세요. 오류: {e}")
    exit()

# --- 메인 봇 클래스 ---
class PeopleAIBot:
    def __init__(self):
        # 봇 ID 가져오기
        try:
            self.bot_id = app.client.auth_test()['user_id']
            logger.info(f"봇 ID({self.bot_id})를 성공적으로 가져왔습니다.")
        except Exception as e:
            logger.error(f"봇 ID 가져오기 실패: {e}")
            self.bot_id = None

        # Gemini API 설정
        self.gemini_model = self.setup_gemini()

        # guide_data.txt 파일 내용을 메모리에 로드
        self.knowledge_base = self.load_knowledge_file()
        
        # 기타 설정
        self.responses = { "searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔"] }
        self.session_tracker = {}

    def setup_gemini(self):
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            logger.error("GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
            return None
        try:
            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel("gemini-2.0-flash")
            logger.info("Gemini API 활성화 완료.")
            return model
        except Exception as e:
            logger.error(f"Gemini 모델 설정 실패: {e}")
            return None

    def load_knowledge_file(self):
        try:
            with open("guide_data.txt", 'r', encoding='utf-8') as f:
                knowledge = f.read()
            logger.info(f"지식 파일 'guide_data.txt' 로드 완료. (총 {len(knowledge)}자)")
            return knowledge
        except FileNotFoundError:
            logger.error("'guide_data.txt' 파일을 찾을 수 없습니다.")
            return ""
        except Exception as e:
            logger.error(f"지식 파일 로드 중 오류 발생: {e}")
            return ""

    def generate_answer(self, query):
        if not self.gemini_model:
            return "AI 모델이 설정되지 않아 답변을 생성할 수 없습니다."
        if not self.knowledge_base:
            return "지식 파일이 비어있어 답변을 드릴 수 없습니다. 'guide_data.txt' 파일을 확인해주세요."

        prompt = f"""
[지시문]
당신은 '중고나라' 회사의 규정과 정보를 정확하게 안내하는 AI 어시스턴트 '피플AI'입니다. 당신의 유일한 임무는 아래 제공된 '[회사 전체 규정 문서]'의 내용만을 기반으로 사용자의 질문에 답변하는 것입니다.

[엄격한 작업 절차]
1. 사용자의 '[질문]'을 주의 깊게 읽고, 질문에 '평점', '가까운', '종류' 등 **조건이나 필터링**이 포함되어 있는지 파악합니다.
2. '[회사 전체 규정 문서]'에서 질문과 관련된 내용을 모두 찾습니다.
3. 만약 질문에 조건이 포함되어 있다면, 찾은 정보 내의 구조화된 데이터(예: '네이버 평점: 4.4', '거리: 약 200m')를 보고 **조건에 맞는 정보만 선별합니다.**
4. 선별된 정보를 바탕으로 사용자가 보기 쉽게 목록 형태로 답변을 생성합니다.
5. 만약 문서에서 질문에 대한 내용을 찾을 수 없다면, **오직 "문의주신 내용은 제가 가진 정보에서는 찾기 어렵네요. 피플팀에 직접 문의해주시겠어요? 📞"** 라고만 답변해야 합니다.

[답변 형식 규칙]
- 한 줄에 한 문장만 작성하여 가독성을 높입니다.
- 굵은 글씨(**) 같은 텍스트 강조는 절대 사용하지 않습니다.

---
[회사 전체 규정 문서]
{self.knowledge_base}
---

[질문]
{query}

[답변]
"""
        try:
            response = self.gemini_model.generate_content(prompt)
            logger.info(f"Gemini 답변 생성 성공. (쿼리: {query[:30]}...)")
            return response.text
        except Exception as e:
            logger.error(f"Gemini API 호출 실패: {e}", exc_info=True)
            return "AI 답변 생성 중 오류가 발생했습니다."

# --- 봇 인스턴스 생성 ---
bot = PeopleAIBot()

# --- Slack 이벤트 핸들러 (채널/DM 분리 버전) ---
@app.event("app_mention")
def handle_app_mention_events(body, say, logger):
    logger.info("app_mention 이벤트를 수신했습니다. (채널 호출)")
    try:
        user_query = body["event"]["text"]
        channel_id = body["event"]["channel"]
        thread_ts = body["event"].get("ts")
        clean_query = user_query.replace(f"<@{bot.bot_id}>", "").strip()

        if not clean_query or len(clean_query) < 2:
            say(text="무엇이 궁금하신가요? 😊", thread_ts=thread_ts)
            return

        thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=thread_ts)
        final_answer = bot.generate_answer(clean_query)
        app.client.chat_update(channel=channel_id, ts=thinking_message['ts'], text=final_answer)

    except Exception as e:
        logger.error(f"app_mention 이벤트 처리 실패: {e}", exc_info=True)
        say(text=f"앗, 오류가 발생했어요. 😢", thread_ts=body["event"].get("ts"))

@app.event("message")
def handle_message_events(body, say, logger):
    if body["event"].get("channel_type") == "im":
        logger.info("DM 메시지 이벤트를 수신했습니다.")
        try:
            user_query = body["event"]["text"]
            user_id = body["event"]["user"]
            channel_id = body["event"]["channel"]
            
            if "subtype" in body["event"] or (bot.bot_id and user_id == bot.bot_id):
                return

            clean_query = user_query.strip()

            if not clean_query or len(clean_query) < 2:
                say("무엇이 궁금하신가요? 😊")
                return
            
            thinking_message = say(random.choice(bot.responses['searching']))
            final_answer = bot.generate_answer(clean_query)
            app.client.chat_update(channel=channel_id, ts=thinking_message['ts'], text=final_answer)

        except Exception as e:
            logger.error(f"DM 메시지 처리 실패: {e}", exc_info=True)
            say("앗, 오류가 발생했어요. 😢")

# --- Flask 라우팅 ---
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check():
    return "피플AI (단순 검색 모드) 정상 작동중! 🟢"

# --- 앱 실행 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
