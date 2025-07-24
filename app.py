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
            logger.error(f"봇 ID 가져오기 실패: {e}")
            self.bot_id = None

        self.gemini_model = self.setup_gemini()
        self.knowledge_base = self.load_knowledge_file()
        self.responses = { "searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔"] }

    def setup_gemini(self):
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            logger.error("GEMINI_API_KEY가 설정되지 않았습니다.")
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
            logger.info(f"지식 파일 로드 완료. (총 {len(knowledge)}자)")
            return knowledge
        except FileNotFoundError:
            logger.error("'guide_data.txt' 파일을 찾을 수 없습니다.")
            return ""
        except Exception as e:
            logger.error(f"지식 파일 로드 중 오류: {e}")
            return ""

    def generate_answer(self, query):
        if not self.gemini_model: return "AI 모델이 설정되지 않아 답변할 수 없습니다."
        if not self.knowledge_base: return "지식 파일이 비어있어 답변할 수 없습니다."
        
        prompt = f"""
[당신의 역할]
당신은 '중고나라' 회사의 피플팀 AI 어시스턴트 '피플AI'입니다. 당신의 임무는 동료의 질문에 명확하고 간결하며, 가독성 높게 답변하는 것입니다.

[답변 생성 원칙]
1.  **핵심 위주 답변**: 사용자의 질문 의도를 파악하여 가장 핵심적인 답변을 먼저 간결하게 제공합니다. 모든 정보를 나열하기보다, 질문에 직접 관련된 내용을 우선으로 합니다.
2.  **정보 출처 절대성**: 모든 답변은 제공된 '[참고 자료]'에만 근거해야 합니다. 자료에 내용이 없으면 "음, 문의주신 부분에 대해서는 제가 지금 바로 명확한 답변을 드리기는 조금 어렵네요. 📞 피플팀 다른 담당자분께 한번 문의해보시는 건 어떨까요?" 와 같이 부드럽게 답변합니다.
3.  **자연스러운 소통**: "참고 자료에 따르면" 같은 표현 없이, 당신이 이미 알고 있는 지식처럼 자연스럽게 설명합니다.

[답변 형식화 최종 규칙]
당신은 반드시 다음 규칙을 지켜 답변을 시각적으로 명확하게 구성해야 합니다. 이는 매우 중요합니다.

1.  **단계별 안내**: 문제 해결 절차나 순서가 중요한 내용은 반드시 번호 목록(1., 2., 3.)을 사용하여 안내합니다.
2.  **정보 나열**: 순서가 중요하지 않은 정보나 여러 옵션을 나열할 때는 글머리 기호(- 또는 *)를 사용합니다.
3.  **정보성 이모지 활용**: 각 내용의 성격에 맞는 이모지를 문장 앞에 붙여 사용자가 내용을 빠르게 파악할 수 있도록 돕습니다.
    - 🔄 절차/단계, ✅ 확인 사항, 💡 해결 방법/팁, ⚠️ 주의사항, 📞 문의 담당자
4.  **문장 나누기**: 모든 문장은 "~다.", "~요." 등으로 끝난 후 반드시 줄바꿈을 합니다.
5.  **강조 금지**: 굵은 글씨(**) 등 텍스트 강조는 절대 사용하지 않습니다.

[좋은 답변 예시]
모니터 연결에 문제가 있으시군요.
아래 순서대로 한번 확인해보시겠어요?

🔄 **모니터 문제 해결 절차**
1.  **케이블 연결 확인**
    - ✅ 모니터 전원 케이블과 PC 연결 케이블(HDMI 등)이 잘 꽂혀 있는지 확인해주세요.
2.  **소프트웨어 충돌 확인 (Mac 사용자)**
    - 💡 VPN(FortiClient)이나 Logitech 관련 프로그램이 실행 중이라면 종료한 후 다시 시도해보세요.
3.  **담당자 문의**
    - ⚠️ 위 방법으로 해결되지 않으면, 더 이상 직접 조치하지 마세요.
    - 📞 피플팀(시현빈 매니저)에게 문의하여 지원을 요청해주세요.
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
            logger.info(f"Gemini 답변 생성 성공. (쿼리: {query[:30]}...)")
            return response.text
        except Exception as e:
            logger.error(f"Gemini API 호출 실패: {e}", exc_info=True)
            return "AI 답변 생성 중 오류가 발생했습니다."

bot = PeopleAIBot()

# --- Slack 이벤트 핸들러 (도움말 기능 추가 및 최종 로직 적용) ---
@app.event("message")
def handle_all_message_events(body, say, logger):
    try:
        event = body["event"]
        user_id = event.get("user")
        
        if "subtype" in event or (bot.bot_id and user_id == bot.bot_id):
            return

        channel_id = event.get("channel")
        text = event.get("text", "")
        thread_ts = event.get("thread_ts")
        message_ts = event.get("ts")
        
        # '도움말' 명령어 처리
        if text.strip() == "도움말":
            logger.info(f"'{user_id}' 사용자가 도움말을 요청했습니다.")
            help_text = """안녕하세요! 저는 중고나라 피플팀의 AI 어시스턴트, *피플AI*입니다. 🤖
회사 생활과 관련된 다양한 정보(복지, 휴가, 업무 절차, 시설 안내 등)에 대해 질문해주시면 신속하게 답변해 드려요.

*📋 피플AI 사용법 안내*

*1. 질문하기*
- DM(개인 메시지)과 채널에서 멘션 없이 편하게 질문해주세요.
- 제 답변은 항상 질문에 대한 스레드(댓글)로 달립니다.

*2. 스레드에서 추가 질문하기*
- 저는 스레드에서 오가는 일반 대화에는 참여하지 않아요.
- 하지만 스레드 안에서 `@피플AI`로 저를 다시 불러주시면, 그 질문에는 이어서 답변해 드립니다!

*💡 예시 질문*
- "플레이북 링크 주소를 알려줘"
- "모니터가 안나오는데 피플팀 담당자는 누구야?"
- "이전 직장 동료를 사내 추천하려면 어떻게 해?"
"""
            reply_ts = thread_ts if thread_ts else message_ts
            say(text=help_text, thread_ts=reply_ts)
            return

        # 스레드 안에서의 대화 처리
        if thread_ts:
            if f"<@{bot.bot_id}>" in text:
                logger.info("스레드 내에서 멘션을 감지하여 응답합니다.")
                clean_query = text.replace(f"<@{bot.bot_id}>", "").strip()
                if not clean_query: return
                
                thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=thread_ts)
                final_answer = bot.generate_answer(clean_query)
                app.client.chat_update(channel=channel_id, ts=thinking_message['ts'], text=final_answer)
            else:
                return # 멘션 없으면 무시
        # 새로운 메시지 처리
        else:
            logger.info("새로운 메시지를 감지했습니다. 스레드를 시작하며 답변합니다.")
            clean_query = text.strip()
            if not clean_query or len(clean_query) < 2: return

            thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=message_ts)
            final_answer = bot.generate_answer(clean_query)
            app.client.chat_update(channel=channel_id, ts=thinking_message['ts'], text=final_answer)

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
