import os
import random
import logging
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
import google.generativeai as genai

# --- 환경 변수 체크 ---
required_env = ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "GEMINI_API_KEY"]
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
        self.knowledge_base = self.load_knowledge_file()
        self.help_text = self.load_help_file()
        self.responses = { "searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔"] }

    def setup_gemini(self):
        try:
            gemini_api_key = os.environ.get("GEMINI_API_KEY")
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
                return f.read()
            logger.info(f"지식 파일 로드 완료.")
        except FileNotFoundError:
            logger.error("'guide_data.txt' 파일을 찾을 수 없습니다.")
            return ""

    def load_help_file(self):
        try:
            with open("help.md", 'r', encoding='utf-8') as f:
                return f.read()
            logger.info("도움말 파일 로드 완료.")
        except FileNotFoundError:
            logger.error("'help.md' 파일을 찾을 수 없습니다.")
            return "도움말 파일을 찾을 수 없습니다."

    def generate_answer(self, query):
        if not self.gemini_model: return "AI 모델이 설정되지 않아 답변할 수 없습니다."
        if not self.knowledge_base: return "지식 파일이 비어있어 답변할 수 없습니다."
        
        prompt = f"""
[당신의 역할]
당신은 '중고나라' 회사의 피플팀 AI 어시스턴트 '피플AI'입니다. 당신의 임무는 동료의 질문에 명확하고 간결하며, 가독성 높게 답변하는 것입니다.

[답변 생성 원칙]
1.  **핵심 위주 답변**: 사용자의 질문 의도를 파악하여 가장 핵심적인 답변을 먼저 간결하게 제공합니다. 모든 정보를 나열하기보다, 질문에 직접 관련된 내용을 우선으로 합니다.
2.  **정보 출처 절대성**: 모든 답변은 제공된 '[참고 자료]'에만 근거해야 합니다. 자료에 내용이 없으면 "음, 문의주신 부분에 대해서는 제가 지금 바로 명확한 답변을 드리기는 조금 어렵네요. 📞 피플팀 다른 담당자분께 한번 문의해보시는 건 어떨까요?" 와 같이 부드럽게 답변합니다.
3.  **후속 질문 안내**: 답변 마지막에는 항상 "더 궁금한 점이 있다면, 이 스레드에서 저를 다시 멘션해주세요." 라는 안내 문구를 추가합니다.

[답변 형식화 최종 규칙]
당신은 반드시 다음 규칙을 지켜 답변을 시각적으로 명확하게 구성해야 합니다. 이는 매우 중요합니다.
1.  **단계별 안내**: 문제 해결 절차나 순서가 중요한 내용은 반드시 번호 목록(1., 2., 3.)을 사용하여 안내합니다.
2.  **정보 나열**: 순서가 중요하지 않은 정보나 여러 옵션을 나열할 때는 글머리 기호(- 또는 *)를 사용합니다.
3.  **정보성 이모지 활용**: 각 내용의 성격에 맞는 이모지를 문장 앞에 붙여 가독성을 높입니다. (예: 🔄 절차, ✅ 확인, 💡 팁, ⚠️ 주의, 📞 문의)
4.  **문장 나누기**: 모든 문장은 "~다.", "~요." 등으로 끝난 후 반드시 줄바꿈을 합니다.
5.  **강조 금지**: 굵은 글씨(**) 등 텍스트 강조는 절대 사용하지 않습니다. 제목이나 소제목도 마찬가지입니다.

[좋은 답변 예시]
모니터 연결에 문제가 있으시군요.
아래 순서대로 한번 확인해보시겠어요?

🔄 모니터 문제 해결 절차
1. 케이블 연결 확인
   - ✅ 모니터 전원 케이블과 PC 연결 케이블(HDMI 등)이 잘 꽂혀 있는지 확인해주세요.
2. 소프트웨어 충돌 확인 (Mac 사용자)
   - 💡 VPN(FortiClient)이나 Logitech 관련 프로그램이 실행 중이라면 종료한 후 다시 시도해보세요.
3. 담당자 문의
   - ⚠️ 위 방법으로 해결되지 않으면, 더 이상 직접 조치하지 마세요.
   - 📞 피플팀(시현빈 매니저)에게 문의하여 지원을 요청해주세요.
더 궁금한 점이 있다면, 이 스레드에서 저를 다시 멘션해주세요.
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
    text = event.get("text", "").strip()
    message_ts = event.get("ts")
    
    if not text or len(text) < 2: return

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
        thread_ts = event.get("thread_ts")
        message_ts = event.get("ts")

        if text == "도움말":
            logger.info(f"'{event.get('user')}' 사용자가 도움말을 요청했습니다.")
            reply_ts = thread_ts if thread_ts else message_ts
            say(text=bot.help_text, thread_ts=reply_ts)
            return

        if thread_ts:
            handle_thread_reply(event, say)
        else:
            handle_new_message(event, say)

    except Exception as e:
        logger.error(f"message 이벤트 처리 중 오류 발생: {e}", exc_info=True)

# --- Flask 라우팅 ---
@flask_app.route("/slack/events", methods=["POST"])
def slack_events(): return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check(): return "피플AI (최종 버전) 정상 작동중! 🟢"

# --- 앱 실행 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
