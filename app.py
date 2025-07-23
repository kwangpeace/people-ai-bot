import os
import random
import logging
import socket
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
import google.generativeai as genai

# --- 로깅 설정 ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler(), logging.FileHandler("people_ai_bot.log")])
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
    logger.critical(f"앱 초기화 실패! 환경 변수를 확인하세요. 오류: {e}")
    exit()

# --- 메인 봇 클래스 ---
class PeopleAIBot:
    def __init__(self):
        try:
            self.bot_id = app.client.auth_test()['user_id']
            logger.info(f"봇 ID({self.bot_id})를 성공적으로 가져왔습니다.")
        except Exception as e:
            logger.error(f"봇 ID를 가져오는 데 실패했습니다. SLACK_BOT_TOKEN을 확인하세요. 오류: {e}")
            self.bot_id = None

        self.gemini_model = self.setup_gemini()
        self.knowledge_base = self.load_knowledge_file()
        
        self.responses = {
            "searching": ["잠시만요... 🕵️‍♀️"],
            "failure": ["앗, 예상치 못한 오류가 발생했어요. 😢\n잠시 후 다시 시도해주세요."]
        }
        self.session_tracker = {}
        logger.info("봇 기능 설정 완료.")

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
            logger.error("'guide_data.txt' 파일을 찾을 수 없습니다. 기본 텍스트로 대체합니다.")
            return "기본 가이드 데이터가 없습니다. 파일을 추가해주세요."
        except Exception as e:
            logger.error(f"지식 파일 로드 중 오류 발생: {e}")
            raise

    def generate_answer(self, query):
        if not self.gemini_model:
            return "AI 모델이 설정되지 않아 답변을 생성할 수 없습니다."
        if not self.knowledge_base:
            return "지식 파일이 비어있어 답변을 드릴 수 없습니다. 'guide_data.txt' 파일을 확인해주세요."
        context = self.knowledge_base[:2000] if len(self.knowledge_base) > 2000 else self.knowledge_base
        prompt = f"""
[당신의 역할]
당신은 '중고나라' 회사의 피플팀 AI 어시스턴트 '피플AI'입니다.
[매우 중요한 규칙]
- **반드시** 아래 제공된 '[회사 규정 전체 내용]'에만 근거해서 답변해야 합니다.
- 당신의 일반 지식이나 외부 정보를 절대 사용하지 마세요.
- 참고 자료에 질문과 관련된 내용이 전혀 없다면, "문의주신 내용은 제가 가진 정보에서는 찾기 어렵네요. 피플팀에 직접 문의해주시겠어요? 📞" 라고만 답변하세요.
- 슬랙(Slack) 가독성에 최적화된 형식으로 답변해주세요.
  1. 모든 문장 끝(~다, ~요 등)에는 줄바꿈을 넣어 한 줄에 한 문장만 표시합니다.
  2. 텍스트를 굵게 만드는 마크다운(**)은 절대 사용하지 마세요.
  3. 이모지는 정보 구분을 위해 제한적으로 사용하세요 (예: ✅, 📅, 💡, ⚠️).
---
[회사 규정 전체 내용]
{context}
---
[직원의 질문]
{query}
[답변]
"""
        try:
            response = self.gemini_model.generate_content(prompt)
            logger.info(f"Gemini 답변 생성 성공. (쿼리: {query[:30]}...)")
            return response.text
        except Exception as e:
            logger.error(f"Gemini API 호출 실패: {e}", exc_info=True)
            return random.choice(self.responses['failure'])

# --- 봇 인스턴스 생성 ---
bot = PeopleAIBot()

# --- Slack 이벤트 핸들러 ---
@app.message(".*")
def handle_message(message, say):
    try:
        user_query = message['text']
        is_im = message.get('channel_type') == 'im'
        is_mentioned = bot.bot_id and f"<@{bot.bot_id}>" in user_query
        
        if is_im or is_mentioned:
            clean_query = user_query.replace(f"<@{bot.bot_id}>", "").strip() if bot.bot_id else user_query.strip()
            if not clean_query or len(clean_query) < 2:
                logger.info(f"너무 짧거나 빈 쿼리 무시됨. 쿼리: '{clean_query}'")
                return

            if "디버그" in clean_query.lower():
                debug_text = f"✅ 제가 지금 알고 있는 내용입니다:\n\n---\n\n{bot.knowledge_base[:2500]}" if bot.knowledge_base else "❌ 제가 지금 알고 있는 내용이 없습니다. 'guide_data.txt' 파일 확인 필요."
                say(debug_text)
            else:
                say(random.choice(bot.responses['searching']))
                response = bot.generate_answer(clean_query)
                say(response)

    except Exception as e:
        logger.error(f"메시지 처리 실패: {e}", exc_info=True)
        say(random.choice(bot.responses['failure']))

# --- Flask 라우팅 ---
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check():
    return "피플AI (디버그 모드) 정상 작동중! 🟡"

# --- 앱 실행 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('0.0.0.0', port)) != 0:
                break
            port += 1
    logger.info(f"Flask 앱을 포트 {port}에서 실행합니다.")
    flask_app.run(host="0.0.0.0", port=port)
