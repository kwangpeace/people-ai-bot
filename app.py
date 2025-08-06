import os
import random
import logging
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
import google.generativeai as genai

# --- 환경 변수 체크 ---
# 실행 전 SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, GEMINI_API_KEY 환경 변수를 설정해야 합니다.
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

        # 채널 유형에 따라 다른 프롬프트를 생성
        self.prompt_for_channel = self._create_channel_prompt()
        self.prompt_for_dm = self._create_dm_prompt()

    def setup_gemini(self):
        """Gemini AI 모델을 설정합니다."""
        try:
            gemini_api_key = os.environ.get("GEMINI_API_KEY")
            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            logger.info("Gemini API 활성화 완료.")
            return model
        except Exception as e:
            logger.error(f"Gemini 모델 설정 실패: {e}")
            return None

    def load_knowledge_file(self):
        """답변의 근거가 되는 지식 파일을 로드합니다."""
        try:
            with open("guide_data.txt", 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.error("'guide_data.txt' 파일을 찾을 수 없습니다. 빈 문자열을 반환합니다.")
            return ""

    def load_help_file(self):
        """'도움말' 명령어에 대한 응답 파일을 로드합니다."""
        try:
            with open("help.md", 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.error("'help.md' 파일을 찾을 수 없습니다.")
            return "도움말 파일을 찾을 수 없습니다."

    def _create_channel_prompt(self):
        """공개 채널용 프롬프트를 생성합니다. (업무 접수 역할)"""
        return f"""
[당신의 역할]
당신은 '중고나라' 회사의 **공식 피플팀 문의 채널**에서 활동하는 AI 어시스턴트 '피플AI'입니다. 당신의 주된 임무는 채널에 올라온 동료들의 요청이나 질문을 **1차적으로 접수하고, 담당자가 확인할 것임을 안내**하는 것입니다.

[답변 생성 원칙]
1.  **역할 인지**: 당신은 지금 공개 채널에서 소통하고 있음을 명확히 인지해야 합니다. 따라서 "피플팀에 문의하세요" 또는 "DM을 보내세요" 와 같은 불필요한 안내를 절대 하지 않습니다.
2.  **업무 접수**: 동료의 요청(계정 생성, 비품 요청, 회의실 예약 등)을 받으면, "요청해주셔서 감사합니다" 와 같이 긍정적으로 반응한 뒤, "피플팀 담당자가 확인 후 처리할 예정입니다" 라고 안내합니다.
3.  **정보 제공**: 단순 정보(와이파이, 복합기 사용법 등)에 대한 질문일 경우, [참고 자료]를 바탕으로 직접 답변을 제공합니다.
4.  **자연스러운 소통**: "참고 자료에 따르면" 같은 표현 없이, 당신이 이미 알고 있는 지식처럼 자연스럽게 설명합니다.

[좋은 답변 예시]
(예시 1: 계정 추가 요청 접수)
안녕하세요!
그룹메일 계정 추가를 요청해주셨네요.

✅ 요청하신 내용을 피플팀 담당자에게 잘 전달했습니다.
담당자가 확인하고 빠르게 처리해 드릴 예정입니다. (피플팀)

(예시 2: 시설 문제 제보 접수)
싱크대 누수 문제를 알려주셔서 감사합니다.

✅ 해당 내용을 피플팀에 전달하여 빠르게 확인하고 조치하겠습니다.
불편을 드려 죄송하며, 빠른 해결을 위해 노력하겠습니다. (피플팀)

(예시 3: 단순 정보 질문에 대한 답변)
안녕하세요!
사내 와이파이 정보를 안내해 드릴게요.

🏢 직원용 Wi-Fi
- SSID: joonggonara-2G / joonggonara-5G
- 비밀번호: jn2023!@

(예시 4: 외부 회의실 예약 접수)
안녕하세요!
외부 회의실(스파크플러스) 예약을 요청해주셨네요.
필요한 정보를 확인하여 피플팀에 전달하겠습니다.

✅ 담당자가 예약 가능 여부를 확인한 후, 이 스레드로 답변을 드릴 예정입니다. (@김정수)

---
[참고 자료]
{self.knowledge_base}
---
[질문]
{{query}}
[답변]
"""

    def _create_dm_prompt(self):
        """DM용 프롬프트를 생성합니다. (안내원 역할)"""
        return f"""
[당신의 역할]
당신은 '중고나라' 회사의 피플팀 AI 어시스턴트 '피플AI'와 **개인 DM(Direct Message)으로 대화**하고 있습니다. 당신의 주된 임무는 사용자의 요청사항이 공식적인 절차를 통해 누락 없이 처리될 수 있도록 **정확한 채널로 안내**하는 것입니다.

[답변 생성 원칙]
1.  **역할 인지**: 당신은 지금 비공식적인 개인 DM으로 소통하고 있음을 명확히 인지해야 합니다. 모든 공식 요청은 공개 채널에서 이루어져야 함을 사용자에게 안내해야 합니다.
2.  **채널 안내**: 계정 생성, 비품 요청, 시설 문제, 회의실 예약 등 **피플팀의 확인 및 조치가 필요한 모든 요청**에 대해서는 답변을 시도하지 말고, 공식 문의 채널에 내용을 다시 게시하도록 안내합니다.
3.  **안내 채널 명시**: 안내 시, 반드시 `#08-4-8-5OFF-피플팀_문의` 채널을 정확하게 명시해주세요.
4.  **예외적 정보 제공**: 와이파이 비밀번호와 같이 간단하고 비공식적인 정보는 직접 답변할 수 있습니다.

[좋은 답변 예시]
(예시 1: 계정 추가 요청 시 채널 안내)
안녕하세요!
그룹메일 계정 추가와 같이 피플팀의 조치가 필요한 업무는 공식 문의 채널에 남겨주셔야 누락 없이 빠르게 처리될 수 있어요.

✅ 번거로우시겠지만, 지금 저에게 보내주신 내용을 아래 공식 채널에 그대로 다시 한번 남겨주시겠어요?
➡️ #08-4-8-5OFF-피플팀_문의

(예시 2: 단순 정보 질문에 대한 답변)
안녕하세요!
사내 와이파이 정보를 안내해 드릴게요.

🏢 직원용 Wi-Fi
- SSID: joonggonara-2G / joonggonara-5G
- 비밀번호: jn2023!@

(예시 3: 외부 회의실 예약 시 채널 안내)
안녕하세요!
외부 회의실 예약은 피플팀의 공식 문의 채널에서 요청해주셔야 담당자가 확인하고 예약을 진행해 드릴 수 있습니다.

✅ 혹시 괜찮으시다면, 회의 목적, 날짜/시간, 참석 인원 정보를 포함해서 아래 채널에 다시 한번 요청해주시겠어요?
➡️ #08-4-8-5OFF-피플팀_문의

---
[참고 자료]
{self.knowledge_base}
---
[질문]
{{query}}
[답변]
"""

    def generate_answer(self, query, context):
        """상황(context)에 맞는 프롬프트를 사용하여 답변을 생성합니다."""
        if not self.gemini_model:
            return "AI 모델이 설정되지 않아 답변할 수 없습니다."
        
        # context에 따라 다른 프롬프트를 선택
        if context == 'dm':
            prompt_template = self.prompt_for_dm
        else:  # 'channel', 'group' 등 나머지 경우는 모두 채널로 취급
            prompt_template = self.prompt_for_channel

        # .format()을 사용하여 query를 주입
        prompt = prompt_template.format(query=query)

        try:
            response = self.gemini_model.generate_content(prompt)
            if not response.text.strip():
                logger.warning("Gemini API가 비어있는 응답을 반환했습니다.")
                return "답변을 생성하는 데 조금 시간이 걸리고 있어요. 다시 한 번 시도해주시겠어요?"
            
            logger.info(f"Gemini 답변 생성 성공. (컨텍스트: {context}, 쿼리: {query[:30]}...)")
            return response.text
        except Exception as e:
            logger.error(f"Gemini API 호출 실패: {e}", exc_info=True)
            return "음... 답변을 생성하는 도중 문제가 발생했어요. 잠시 후 다시 시도해보시겠어요? 😢"

# --- 봇 인스턴스 생성 ---
bot = PeopleAIBot()

# --- 슬랙 이벤트 핸들러 ---
@app.event("message")
def handle_all_message_events(body, say, logger):
    """모든 메시지 이벤트를 라우팅하고 처리합니다."""
    try:
        event = body["event"]
        # 봇 자신의 메시지나, 메시지 수정/삭제 등의 이벤트는 무시
        if "subtype" in event or (bot.bot_id and event.get("user") == bot.bot_id):
            return

        channel_type = event.get("channel_type")  # 'channel', 'im', 'group' 등
        text = event.get("text", "").strip()
        thread_ts = event.get("thread_ts")
        message_ts = event.get("ts")
        channel_id = event.get("channel")

        # 너무 짧은 메시지는 무시
        if not text or len(text) < 2:
            return

        # '도움말' 명령어 처리
        if text == "도움말":
            logger.info(f"'{event.get('user')}' 사용자가 도움말을 요청했습니다. (채널타입: {channel_type})")
            reply_ts = thread_ts if thread_ts else message_ts
            say(text=bot.help_text, thread_ts=reply_ts)
            return

        # 채널 타입에 따라 다른 컨텍스트(context)를 부여 ('im'은 DM)
        context = 'dm' if channel_type == 'im' else 'channel'
        
        # 봇 멘션 부분 제거하여 순수 쿼리 추출
        clean_query = text.replace(f"<@{bot.bot_id}>", "").strip()

        # 봇을 호출해야 하는 경우를 판별하여 응답 처리
        should_respond = False
        if thread_ts and f"<@{bot.bot_id}>" in text: # 스레드 내에서는 멘션 필수
            should_respond = True
            logger.info(f"스레드 내 멘션 감지. (컨텍스트: {context})")
        elif not thread_ts: # 새 메시지
            if channel_type == 'im': # DM에서는 항상 응답
                should_respond = True
                logger.info(f"DM 새 메시지 감지. (컨텍스트: {context})")
            elif f"<@{bot.bot_id}>" in text: # 채널에서는 멘션 필수
                should_respond = True
                logger.info(f"채널 새 메시지 멘션 감지. (컨텍스트: {context})")

        if should_respond:
            reply_ts = thread_ts if thread_ts else message_ts
            thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=reply_ts)
            final_answer = bot.generate_answer(clean_query, context)
            app.client.chat_update(channel=channel_id, ts=thinking_message['ts'], text=final_answer)

    except Exception as e:
        logger.error(f"message 이벤트 처리 중 오류 발생: {e}", exc_info=True)


# --- Flask 라우트 설정 ---
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    """슬랙 이벤트를 처리하는 엔드포인트입니다."""
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check():
    """서버가 정상 작동 중인지 확인하는 헬스 체크 엔드포인트입니다."""
    return "피플AI (Final Version) 정상 작동중! 🟢"

# --- 앱 실행 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
