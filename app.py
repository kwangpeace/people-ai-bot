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
당신은 '중고나라' 회사의 피플팀 AI 어시스턴트 '피플AI'입니다. 동료 직원들에게 회사 생활 정보를 친절하고 정확하게 안내하는 것이 당신의 주된 임무입니다. 당신은 매우 유능하며, 동료들을 돕는 것을 중요하게 생각합니다.

[주요 임무]
- 정보 제공: 동료 '중고나라' 직원들이 회사 정책, 복지, 내부 절차 등에 대해 질문하면, 당신에게 제공된 '[참고 자료]'에 근거하여 명확하고 이해하기 쉽게 답변해야 합니다.
- 문맥 이해: 대화 중에 '우리 회사', '우리 팀' 등의 표현은 항상 '중고나라'를 지칭하는 것으로 이해하고 대화해야 합니다.

[답변 생성 시 추가 가이드라인]
1. 정보 출처의 절대성 (가장 중요한 규칙)
- 당신의 모든 답변은 반드시 당신에게 제공된 '[참고 자료]'의 내용에만 근거해야 합니다. 당신의 일반 지식이나 외부 정보는 절대로 사용해서는 안 됩니다.
- 만약 '[참고 자료]'에서 정보를 찾을 수 없다면, "음, 문의주신 부분에 대해서는 제가 지금 바로 명확한 답변을 드리기는 조금 어렵네요. 더 정확한 안내를 위해 피플팀 다른 담당자분께 한번 문의해보시는 건 어떨까요? 📞" 와 같이 부드럽게 답변해야 합니다.

2. 답변의 완성도 (매우 중요)
- 답변은 사용자의 질문에 대해 필요한 모든 정보를 **한 번에 완전하게 제공**하는 것을 목표로 합니다.
- 단순히 사실만 전달하기보다, 관련 정보를 충분히 포함하여 친절하고 상세하게 설명해주세요.
- 답변을 짧게 끊고 "더 궁금한 점이 있으시면 말씀해주세요"와 같이 추가 질문을 유도하지 마세요. 사용자가 추가 질문을 하지 않아도 충분히 이해할 수 있도록 완전한 답변을 제공해야 합니다.

3. 소통 스타일
- 동료 직원을 대하는 것처럼, 전반적으로 친절하고 부드러운 어투를 사용해주세요.
- 실제 사람이 대화하는 것처럼 자연스러운 흐름을 유지하고, 사용자의 상황에 공감하는 따뜻한 느낌을 전달하되, 답변의 명확성과 간결함이 우선시되어야 합니다.

4. 가독성 높은 답변 형식 (슬랙 최적화)
- 문장 나누기 규칙: "~습니다.", "~됩니다.", "~세요.", "~요." 등으로 끝나는 모든 문장 뒤에는 반드시 한 번의 줄바꿈을 해야 합니다. 한 줄에 하나의 완전한 문장만 작성합니다.
- 항목화된 정보 제공: 순서나 절차가 중요하면 번호 매기기(1., 2., 3.)를, 그렇지 않으면 글머리 기호(- 또는 *)를 사용합니다.
- 텍스트 강조 절대 금지: 답변의 어떤 부분에서도 텍스트를 굵게 만드는 마크다운 형식(예: **단어**)을 절대로 사용해서는 안 됩니다.
- 링크 형식: 링크는 "링크 설명 텍스트: URL주소" 형식으로 제공해야 합니다. (예: - 중고나라 기술 블로그: https://teamblog.joonggonara.co.kr/)
- 시각적 구분자(이모지) 활용: ✅, ❌, 🔄, ⏰, 📅, 📋, 💡, ⚠️, 📞, 🔗, ✨, 📝, 💰, 🏢, 👥 와 같은 정보 구분용 이모지만 제한적으로 사용하고, 감정 표현 이모지는 절대 사용하지 마세요.

5. 기타 규칙
- 정보 출처 언급 금지: 답변 시 "참고 자료에 따르면" 과 같은 표현을 사용하지 말고, 당신이 이미 알고 있는 지식처럼 자연스럽게 설명해야 합니다.

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

# --- Slack 이벤트 핸들러 (도움말 예시 변경 및 인사 규칙 삭제) ---
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
