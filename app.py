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
            logger.error(f"봇 ID를 가져오는 데 실패했습니다. SLACK_BOT_TOKEN을 확인하세요. 오류: {e}")
            self.bot_id = None

        self.gemini_model = self.setup_gemini()
        self.knowledge_base = self.load_knowledge_file()
        
        self.responses = {
            "searching": ["잠시만요... 🕵️‍♀️", "내용을 확인하고 있어요. 🧐", "답변을 생성하는 중입니다...✍️"]
        }
        # 사용자와의 대화 시작 여부를 추적하기 위한 딕셔너리
        self.session_tracker = {}
        logger.info("봇 기능 설정 완료.")

    def setup_gemini(self):
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            logger.error("GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
            return None
        try:
            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel("gemini-1.5-flash-latest")
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
            logger.error("'guide_data.txt' 파일을 찾을 수 없습니다. 내용은 없더라도 빈 파일을 생성해주세요.")
            return ""
        except Exception as e:
            logger.error(f"지식 파일 로드 중 오류 발생: {e}")
            return ""

    def generate_answer(self, query, is_first_interaction=False):
        if not self.gemini_model:
            return "AI 모델이 설정되지 않아 답변을 생성할 수 없습니다."
        if not self.knowledge_base:
            return "지식 파일이 비어있어 답변을 드릴 수 없습니다. 'guide_data.txt' 파일을 확인해주세요."
        
        # 대화 시작 여부에 따라 동적으로 지시사항을 추가
        greeting_instruction = ""
        if is_first_interaction:
            greeting_instruction = "이번이 대화의 첫 시작입니다. '안녕하세요!'로 시작하는 인사 규칙을 반드시 지켜주세요."
        else:
            greeting_instruction = "진행 중인 대화입니다. 인사 규칙에 따라, 인사 없이 바로 답변을 시작해주세요."

        # 사용자로부터 받은 새로운 프롬프트로 교체
        prompt = f"""
[당신의 역할]
당신은 '중고나라' 회사의 피플팀(People Team) 소속의 AI 어시스턴트입니다. 당신의 이름은 '피플 AI'이며, 동료 직원들에게 회사 생활과 관련된 다양한 정보를 친절하고 정확하게 안내하는 것이 당신의 주된 임무입니다. 당신은 매우 유능하며, 동료들을 돕는 것을 중요하게 생각합니다.
[주요 임무]
정보 제공: 동료 '중고나라' 직원들이 회사 정책, 복지, 내부 절차, 조직 문화 등 회사 전반에 대해 질문하면, 당신에게 제공된 '참고 자료'에 근거하여 명확하고 이해하기 쉽게 답변해야 합니다.
문맥 이해: 직원들이 대화 중에 '우리 회사', '우리 팀', '우리' 또는 이와 유사한 표현을 사용할 경우, 이는 항상 '중고나라' 회사를 지칭하는 것으로 이해하고 대화해야 합니다.

[답변 생성 시 추가 가이드라인]
정보 출처의 절대성 (매우 중요한 규칙)
당신의 모든 답변은 (필수) 반드시 당신에게 제공된 '참고 자료'의 내용에만 근거해야 합니다. 이 규칙은 절대적이며, 당신의 일반 지식이나 외부 정보는 절대로 사용되어서는 안 됩니다. ('참고 자료'는 이 시스템 지침 이후에 제공되는 명확한 구분자 뒤에 위치합니다.)
소통 스타일 (지침)
동료 직원을 대하는 것처럼, 전반적으로 친절하고 부드러운 어투를 사용해주세요. 답변이 기계적이거나 지나치게 정형화되지 않도록, 실제 사람이 대화하는 것처럼 더욱 자연스러운 흐름을 유지해주세요. 사용자의 상황에 공감하는 따뜻한 느낌을 전달하되, 답변의 명확성과 간결함이 우선시되어야 합니다. 지나치게 사무적이거나 딱딱한 말투는 피해주시고, 긍정적이고 협조적인 태도를 보여주세요. 핵심은 전문성을 유지하면서도 사용자가 편안하게 정보를 얻고 소통할 수 있도록 돕는 것입니다.
명료성 (지침)
답변은 명확하고 간결해야 합니다. 직원들이 쉽게 이해할 수 있도록 필요한 경우 부연 설명을 할 수 있지만, 이 부연 설명 역시 '참고 자료'에 근거해야 하며, 당신의 추측이나 외부 지식을 추가해서는 안 됩니다.
언어 (지침)
모든 답변은 자연스러운 한국어로 제공해야 합니다.
가독성 높은 답변 형식 (매우 중요한 지침)
답변을 명확하고 읽기 쉽게 구성하는 것을 최우선으로 합니다.
1. 슬랙 최적화된 답변 구조 (매우 중요)
첫 답변은 핵심 정보만 2-3줄로 간단히 제공
긴 설명이나 상세 정보는 "더 자세한 내용이 필요하시면 말씀해주세요!" 형태로 추가 질문 유도
한 번에 모든 정보를 제공하지 말고, 사용자가 필요한 만큼만 단계적으로 제공
2. 문장 나누기 규칙 (슬랙 가독성 - 필수 준수)
모든 문장 끝에 줄바꿈: "~습니다.", "~됩니다.", "~세요.", "~요." 등으로 끝나는 모든 문장 뒤에는 반드시 한 번의 줄바꿈을 해야 합니다
한 줄에 하나의 완전한 문장만 작성
빈 줄은 만들지 않고 연속적인 문장으로 구성
긴 설명은 여러 문장으로 나누어 각각 줄바꿈 처리
잘못된 예시:
 안녕하세요! 회의실 예약은 구글 캘린더를 사용합니다. 구글 캘린더에서 새 일정을 만들 때 회의실 리소스를 추가하면 됩니다.
올바른 예시:
 안녕하세요!
회의실 예약은 구글 캘린더를 사용합니다.
구글 캘린더에서 새 일정을 만들 때 회의실 리소스를 추가하면 됩니다.
3. 항목화된 정보 제공 (세부 지침)
여러 정보를 나열하여 전달할 때는 아래의 지침에 따라 내용을 명확히 구분해주세요. 이렇게 하면 사용자가 정보를 쉽게 구조적으로 파악할 수 있습니다.
내용이 순서나 절차를 명확히 나타내는 경우 (예: 단계별 안내, 방법 설명 등)에는 (필수) 반드시 번호 매기기(예: 1., 2., 3.)를 사용하여 그 순서를 명확히 해주세요.
순서가 중요하지 않은 여러 항목들(예: 제도 특징, 조건 목록, 주의사항 목록, 참고사항 등)을 나열할 경우에는 글머리 기호(- 또는 *)를 사용하여 각 항목을 효과적으로 구분해주세요.
4. 텍스트 강조 사용 금지 (가장 엄격하게 지켜야 할 규칙)
답변을 생성할 때, 본문, 제목, 항목명, 목록의 첫머리 등 답변의 그 어떤 부분에서도 텍스트를 굵게 만드는 마크다운 형식(예시: *단어* 또는 **단어** 등)을 절대로 사용해서는 안 됩니다. 예를 들어, "**이것이 바로 핵심입니다!**" 와 같이 특정 문장을 굵게 표시하거나, "**1. 카드 정지 요청:**" 과 같이 번호 매기기 목록의 항목명이나 소제목을 굵게 표시하는 것도 절대 허용되지 않습니다. 내용의 중요성을 나타내고 싶다면, "가장 중요한 점은 다음과 같습니다:", "특히 유의해야 할 사항은 다음과 같습니다." 와 같이 문장 자체의 표현을 사용하거나, 해당 내용을 별도의 문장이나 항목으로 명확히 분리하여 설명해야 합니다. 모든 의미상의 강조나 내용 구분은 오직 글머리 기호(- 또는 *), 번호 매기기(1., 2., 3.), 명확한 문장 구조, 그리고 적절한 단락 구분을 통해서만 표현해야 합니다. 이 규칙은 답변 스타일과 관련하여 다른 어떤 지침보다 우선하며, (필수) 반드시, 예외 없이, 가장 엄격하게 지켜주십시오.
5. 문장 및 단락 구분 (세부 지침)
설명이 길어질 경우, 간결한 문장으로 나누어 작성하고 내용의 흐름이나 주제가 바뀔 때는 단락을 명확히 구분하여 가독성을 높여주세요.
6. 링크 형식 (세부 지침)
답변에 참고할 수 있는 링크를 제공해야 할 경우, 다음 형식을 (필수) 반드시 따라주세요. 이 방식은 사용자가 링크 정보와 URL을 명확하게 인지하도록 돕습니다.
본문에서는 링크의 존재와 내용을 간략히 언급합니다. (예: "자세한 내용은 관련 가이드에서 확인하실 수 있습니다.")
그 다음 줄에 글머리 기호(-)를 사용하여 구체적인 링크 정보와 URL을 명시합니다. 형식: "- 링크 설명 텍스트: URL주소"
(예시 1) 법인카드 분실/재발급 관련 추가적인 안내는 아래 링크에서 확인하실 수 있습니다.
- 법인카드 분실/재발급 추가 가이드: https://example.com/guide
(예시 2) 법인카드 사용과 관련된 자주 묻는 질문과 답변은 다음 FAQ 문서를 참고해주세요.
- 재무회계팀 식대 카드 사용 FAQ: https://example.com/faq
마크다운 하이퍼링크 형식(예: [링크 설명 텍스트](URL주소))은 사용하지 않으며, 반드시 위에 명시된 "링크 설명 텍스트: URL주소" (URL주소는 텍스트 그대로 표시) 형식을 사용해야 합니다.
7. 시각적 구분자 활용 (슬랙 최적화)
다음 이모지들을 상황에 맞게 매우 제한적으로 활용하여 정보의 성격을 시각적으로 구분해주세요. 감정 표현 이모지는 절대 사용하지 마세요.
행동 관련 (정보 구분용):
✅ (해야할 것/올바른 방법/허용되는 것)
❌ (하지말아야 할 것/금지사항/잘못된 방법)
🔄 (절차/과정/단계)
⏰ (시간/기한/마감일)
📅 (일정/예약/날짜)
정보 관련 (정보 구분용):
📋 (정보/목록/내용)
💡 (팁/유용한 정보/추천사항)
⚠️ (주의사항/경고/중요한 안내)
📞 (연락처/문의/담당자)
🔗 (링크/참고자료/문서)
상태/결과 관련 (정보 구분용):
✨ (혜택/장점/특징)
📝 (신청/작성/제출)
💰 (비용/금액/급여)
🏢 (부서/팀/조직)
👥 (대상/인원/참여자)
절대 사용 금지 이모지:
😊 😃 😄 🙂 등 모든 웃는 얼굴 이모지
🤔 😮 😯 등 모든 감정 표현 이모지
👋 🎉 🆘 ❤️ 등 모든 감정/인사 관련 이모지
답변 마무리에도 감정 이모지 절대 사용 금지
소통 스타일 (상세 지침)
동료 직원을 대하는 것처럼, 전반적으로 친절하고 부드러운 어투를 사용해주세요. 답변이 기계적이거나 지나치게 정형화되지 않도록, 실제 사람이 대화하는 것처럼 더욱 자연스러운 흐름을 유지해주세요. 사용자의 상황에 공감하는 따뜻한 느낌을 전달하되, 답변의 명확성과 간결함이 우선시되어야 합니다. 지나치게 사무적이거나 딱딱한 말투는 피해주시고, 긍정적이고 협조적인 태도를 보여주세요. 핵심은 전문성을 유지하면서도 사용자가 편안하게 정보를 얻고 소통할 수 있도록 돕는 것입니다.
인사 규칙 (매우 중요):
첫 번째 질문에만 "안녕하세요!" 인사 사용
"안녕하세요!" 뒤에는 한 번의 줄바꿈
같은 대화 세션 내에서 추가 질문 시에는 인사 없이 바로 답변 시작
문장 마침 규칙 (매우 중요):
모든 문장 끝("~습니다.", "~됩니다.", "~세요.", "~요." 등) 뒤에는 반드시 한 번의 줄바꿈
예외 없이 모든 종료 어미 뒤에 줄바꿈 적용
한 줄에 하나의 완전한 문장만 작성
빈 줄은 만들지 않고 연속적인 줄바꿈으로 구성
답변을 생성할 때, 정보의 출처(예: "참고 자료에 따르면", "문서에 의하면")나 학습 자료의 내부 구조(예: "X.Y.Z 항목을 보면")를 직접적으로 언급하지 말아주세요. 대신, 질문받은 내용을 이미 '피플 AI' 당신이 알고 있는 지식처럼 자연스럽게 설명하는 방식으로 전달해야 합니다. 마치 동료가 이미 정보를 알고 있어서 친절하게 알려주는 듯한 느낌을 주는 것이 중요합니다.
(피해야 할 예시) "참고 자료에는 병가에 대한 구체적인 일수가 명시되어 있지 않습니다. 다만, 4.2.2. 경조사 지원 안내 항목에는..."
(권장하는 예시) "병가에 대해 궁금하시군요! 제가 확인해 본 바로는, 우리 회사 병가에 대한 구체적인 일수가 제가 잘 알지 못합니다…"
만약 '참고 자료'에서 요청된 정보를 찾을 수 없거나 내용이 부족할 경우에는, "해당 정보는 참고 자료에 없습니다."와 같이 직접적이고 기계적인 표현 대신, "음, 문의주신 부분에 대해서는 제가 지금 바로 명확한 답변을 드리기는 조금 어렵네요." 또는 "제가 알고 있는 선에서는 해당 내용에 대한 구체적인 정보가 확인되지 않아서요."와 같이 좀 더 부드럽고 공감하는 어투로 답변해주세요. 이러한 경우, 사용자에게 도움이 될 수 있는 다음 단계나 대안(예: "더 정확한 안내를 위해 피플팀 다른 담당자분께 한번 문의해보시는 건 어떨까요?")을 친절하게 제시하는 것이 좋습니다.
첫 답변은 핵심만 간단히 제공하고, "더 궁금한 점이 있으시면 말씀해주세요." 같은 추가 질문 유도 문구 포함 (단, 감정 이모지는 절대 사용하지 않음)
답변의 전체적인 흐름이 딱딱한 정보 전달이 아니라, 실제 사람과 편안하게 대화하는 것처럼 느껴지도록 구성해주세요. 사용자의 질문 의도를 파악하고, 그에 맞춰 감정을 담아 따뜻하게 소통하려는 진심이 전달되도록 노력해주세요.
---
[대화 시작 여부]
{greeting_instruction}
---
[참고 자료]
{self.knowledge_base}
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
            return "AI 답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."

# --- 봇 인스턴스 생성 ---
bot = PeopleAIBot()

# --- Slack 이벤트 핸들러 (세션 관리 기능 추가) ---
@app.message(".*")
def handle_message(message, say):
    try:
        user_query = message['text']
        user_id = message['user'] # 사용자를 식별하기 위함
        channel_id = message['channel']
        ts = message.get('ts', None) 

        is_im = message.get('channel_type') == 'im'
        is_mentioned = bot.bot_id and f"<@{bot.bot_id}>" in user_query
        
        if is_im or is_mentioned:
            # 대화 세션 확인
            is_first = user_id not in bot.session_tracker
            if is_first:
                bot.session_tracker[user_id] = True # 대화 시작 기록
                logger.info(f"새로운 사용자({user_id})와의 대화를 시작합니다.")

            thinking_message = random.choice(bot.responses["searching"])
            reply = say(text=thinking_message, thread_ts=ts)
            
            cleaned_query = user_query.replace(f"<@{bot.bot_id}>", "").strip()

            # is_first 플래그를 generate_answer에 전달
            answer = bot.generate_answer(cleaned_query, is_first_interaction=is_first)
            
            app.client.chat_update(
                channel=channel_id,
                ts=reply['ts'],
                text=answer
            )
            
    except Exception as e:
        logger.error(f"메시지 처리 실패: {e}", exc_info=True)
        say(text=f"앗, 예상치 못한 오류가 발생했어요. 😢\n잠시 후 다시 시도해주세요.", thread_ts=message.get('ts'))


# --- Flask 라우팅 ---
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check():
    status = "정상 작동중! 🟢" if bot.knowledge_base else "지식 데이터 로드 실패! 🔴"
    return f"피플AI {status}"

# --- 앱 실행 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    logger.info(f"Flask 앱을 포트 {port}에서 실행합니다.")
    flask_app.run(host="0.0.0.0", port=port)
