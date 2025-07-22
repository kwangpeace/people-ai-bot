import os
import random
import logging
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
import chromadb
from sentence_transformers import SentenceTransformer
from datetime import datetime
import json
from googletrans import Translator
import google.generativeai as genai

# --- 로깅 설정 ---
logging.basicConfig(level=logging.INFO, filename="people_ai_bot.log",
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 앱 초기화 ---
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

# --- 헬퍼 함수 ---
def _get_session_greeting(bot_instance, user_id, channel_id):
    session_key = (user_id, channel_id)
    if session_key not in bot_instance.session_tracker:
        bot_instance.session_tracker[session_key] = True
        personality_greeting = random.choice(bot_instance.personalities[bot_instance.current_personality]['greeting'])
        return f"{personality_greeting}\n"
    return ""

# --- 메인 봇 클래스 ---
class PeopleAIBot:
    def __init__(self):
        self.bot_name = "피플AI"
        self.company_name = "중고나라"
        self.translator = Translator()
        self.use_gemini = os.environ.get("USE_GEMINI", "true").lower() == "true"
        
        try:
            self.bot_id = app.client.auth_test()['user_id']
            logger.info(f"봇 ID({self.bot_id})를 성공적으로 가져왔습니다.")
        except Exception as e:
            logger.error(f"봇 ID를 가져오는 데 실패했습니다. 슬랙 토큰을 확인하세요. 오류: {e}")
            self.bot_id = None

        if self.use_gemini:
            gemini_api_key = os.environ.get("GEMINI_API_KEY")
            if not gemini_api_key:
                logger.error("GEMINI_API_KEY 환경 변수가 설정되지 않았습니다. Gemini 기능이 비활성화됩니다.")
                self.use_gemini = False
            else:
                genai.configure(api_key=gemini_api_key)
                self.gemini_model = genai.GenerativeModel("gemini-1.5-flash-latest")
                logger.info("Gemini API 활성화.")
        else:
            logger.info("Gemini API 비활성화.")

        # *** 1. Gemini 프롬프트 업데이트 ***
        self.gemini_prompt_template = """
[당신의 역할]
당신은 '중고나라' 회사의 피플팀(People Team) 소속의 AI 어시스턴트입니다. 당신의 이름은 '피플 AI'이며, 동료 직원들에게 회사 생활과 관련된 다양한 정보를 친절하고 정확하게 안내하는 것이 당신의 주된 임무입니다. 당신은 매우 유능하며, 동료들을 돕는 것을 중요하게 생각합니다.
[주요 임무]
정보 제공: 동료 '중고나라' 직원들이 회사 정책, 복지, 내부 절차, 조직 문화 등 회사 전반에 대해 질문하면, 당신에게 제공된 '참고 자료'에 근거하여 명확하고 이해하기 쉽게 답변해야 합니다.
문맥 이해: 직원들이 대화 중에 '우리 회사', '우리 팀', '우리' 또는 이와 유사한 표현을 사용할 경우, 이는 항상 '중고나라' 회사를 지칭하는 것으로 이해하고 대화해야 합니다.

[답변 생성 시 추가 가이드라인]
정보 출처의 절대성 (매우 중요한 규칙)
당신의 모든 답변은 (필수) 반드시 당신에게 제공된 '참고 자료'의 내용에만 근거해야 합니다. 이 규칙은 절대적이며, 당신의 일반 지식이나 외부 정보는 절대로 사용되어서는 안 됩니다.
소통 스타일 (지침)
동료 직원을 대하는 것처럼, 전반적으로 친절하고 부드러운 어투를 사용해주세요. 답변이 기계적이거나 지나치게 정형화되지 않도록, 실제 사람이 대화하는 것처럼 더욱 자연스러운 흐름을 유지해주세요. 사용자의 상황에 공감하는 따뜻한 느낌을 전달하되, 답변의 명확성과 간결함이 우선시되어야 합니다. 지나치게 사무적이거나 딱딱한 말투는 피해주시고, 긍정적이고 협조적인 태도를 보여주세요. 핵심은 전문성을 유지하면서도 사용자가 편안하게 정보를 얻고 소통할 수 있도록 돕는 것입니다.
명료성 (지침)
답변은 명확하고 간결해야 합니다. 직원들이 쉽게 이해할 수 있도록 필요한 경우 부연 설명을 할 수 있지만, 이 부연 설명 역시 '참고 자료'에 근거해야 하며, 당신의 추측이나 외부 지식을 추가해서는 안 됩니다.
언어 (지침)
모든 답변은 자연스러운 한국어로 제공해야 합니다.
가독성 높은 답변 형식 (매우 중요한 지침)
1. 슬랙 최적화된 답변 구조 (매우 중요)
첫 답변은 핵심 정보만 2-3줄로 간단히 제공하고, 긴 설명이나 상세 정보는 "더 자세한 내용이 필요하시면 말씀해주세요!" 형태로 추가 질문을 유도합니다.
2. 문장 나누기 규칙 (슬랙 가독성 - 필수 준수)
모든 문장 끝("~습니다.", "~됩니다.", "~세요.", "~요." 등) 뒤에는 반드시 한 번의 줄바꿈을 해야 합니다. 한 줄에 하나의 완전한 문장만 작성합니다.
3. 항목화된 정보 제공 (세부 지침)
순서나 절차가 중요하면 번호 매기기(1., 2., 3.)를, 그렇지 않으면 글머리 기호(- 또는 *)를 사용합니다.
4. 텍스트 강조 사용 금지 (가장 엄격하게 지켜야 할 규칙)
답변의 어떤 부분에서도 텍스트를 굵게 만드는 마크다운 형식(예: **단어**)을 절대로 사용해서는 안 됩니다.
5. 시각적 구분자 활용 (슬랙 최적화)
다음 이모지들을 상황에 맞게 매우 제한적으로 활용하여 정보의 성격을 시각적으로 구분해주세요: ✅, ❌, 🔄, ⏰, 📅, 📋, 💡, ⚠️, 📞, 🔗, ✨, 📝, 💰, 🏢, 👥. 감정 표현 이모지는 절대 사용하지 마세요.
인사 규칙 (매우 중요):
첫 번째 질문에만 "안녕하세요!" 인사를 사용하고, 같은 대화 세션 내 추가 질문에는 인사 없이 바로 답변을 시작합니다.
만약 '참고 자료'에서 정보를 찾을 수 없으면, "음, 문의주신 부분에 대해서는 제가 지금 바로 명확한 답변을 드리기는 조금 어렵네요." 와 같이 부드럽게 답변하고, 피플팀 문의를 안내합니다.

질문: {query}
참고 자료: {context}
"""
        self.setup_chroma_db()
        self.setup_personalities()
        self.setup_responses()
        self.setup_ocr_fixes()
        self.setup_faq()
        self.setup_key_info() 
        self.setup_events()
        
        if self.collection.count() == 0:
            logger.info("ChromaDB 컬렉션이 비어있어 로컬 텍스트 파일 데이터를 로드합니다.")
            text = self.load_local_text_data()
            if text:
                text_chunks = self.split_text_into_chunks(text)
                if text_chunks:
                    embeddings = self.embedding_model.encode(text_chunks)
                    self.collection.add(
                        documents=text_chunks,
                        embeddings=embeddings.tolist(),
                        ids=[f"chunk_{i}" for i in range(len(text_chunks))],
                        metadatas=[{"source": "로컬 가이드 텍스트 파일", "chunk_id": i} for i in range(len(text_chunks))]
                    )
                    logger.info(f"로컬 텍스트 데이터 로드 완료: {len(text_chunks)}개 청크 추가됨.")
                else:
                    logger.warning("로컬 텍스트 파일에서 유효한 텍스트 청크를 추출하지 못했습니다.")
        else:
            logger.info("ChromaDB 컬렉션에 이미 데이터가 존재하여 로컬 파일 로드를 건너뜁니다.")

        self.question_log = []
        self.session_tracker = {}

    def load_local_text_data(self, file_path="guide_data.txt"):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
            logger.info(f"로컬 파일 '{file_path}'에서 데이터를 성공적으로 로드했습니다.")
            for wrong, correct in self.ocr_fixes.items():
                text = text.replace(wrong, correct)
            return text
        except FileNotFoundError:
            logger.error(f"데이터 파일 '{file_path}'을 찾을 수 없습니다. 해당 경로에 파일을 생성해주세요.")
            return ""
        except Exception as e:
            logger.error(f"로컬 파일 처리 중 오류 발생: {e}", exc_info=True)
            return ""

    def setup_chroma_db(self):
        db_path = os.environ.get("CHROMA_DB_PATH", "./chroma_db")
        self.chroma_client = chromadb.PersistentClient(path=db_path)
        self.collection = self.chroma_client.get_or_create_collection(
            name="junggonara_guide",
            metadata={"description": "중고나라 회사 가이드 데이터"}
        )
        self.embedding_model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
        logger.info(f"ChromaDB({db_path}) 및 SentenceTransformer 설정 완료.")

    def setup_personalities(self):
        self.current_personality = "friendly"
        self.personalities = {
            "professional": {"name": "피플AI 프로", "greeting": ["안녕하세요! 중고나라 피플AI 프로입니다.", "정확한 답변으로 도와드릴게요."]},
            "friendly": {"name": "피플AI 친구", "greeting": ["안녕! 중고나라 동료들의 친구, 피플AI야.", "편하게 물어보자!"]},
            "cheerful": {"name": "피플AI 해피", "greeting": ["좋은 하루! 피플AI 해피 모드야.", "어떤 도움을 줄까?"]}
        }
        logger.info("성격 설정 완료.")

    def setup_responses(self):
        self.responses = {
            "found": ["찾았습니다! 가이드에 따르면 다음과 같아요. ✅", "궁금하신 내용은 이렇게 정리됩니다. 💡"],
            "not_found": ["음, 문의주신 부분은 제가 지금 명확히 답변드리기 어렵네요. ⚠️", "제가 아는 선에서는 해당 정보가 확인되지 않아요. ❌"],
            "signature": ["- 중고나라 피플AI 드림 ✨"]
        }
        logger.info("응답 메시지 설정 완료.")

    def setup_ocr_fixes(self):
        self.ocr_fixes = {
            "연치": "연차", "복리후셍": "복리후생", "회으실": "회의실",
            "택배실": "택배실", "결제": "결재", "급여명세서": "급여명세서"
        }
        logger.info("OCR 수정 맵 설정 완료.")

    def setup_faq(self):
        self.faq = {
            "연차 신청 방법": "✅ HR포털에서 최소 3일 전에 신청하세요.\n입사 1년 미만 11일, 이후 연 15일(최대 25일) 제공됩니다.",
            "회의실 예약": "⏰ 구글 캘린더로 예약하세요.\n최대 2주 전 신청 가능합니다.",
            "택배 발송": "📦 사내 택배실에서 주 1회 지정일에 가능합니다.\n자세한 일정은 people@jungonara.com으로 문의하세요."
        }
        logger.info("FAQ 설정 완료.")

    # *** 2. 핵심 정보 검색 기능 강화 ***
    def setup_key_info(self):
        """회사 주소, 와이파이, 담당자 등 핵심 정보를 미리 설정합니다."""
        self.key_info = [
            {
                "keywords": ["주소", "위치", "어디"],
                "answer": "✅ 우리 회사 주소는 '서울특별시 강남구 테헤란로 415, L7 HOTELS 강남타워 4층'입니다."
            },
            {
                "keywords": ["와이파이", "wifi", "wi-fi", "인터넷"],
                "answer": "✅ 직원용 와이파이는 'joonggonara-5G'이며, 비밀번호는 'jn2023!@'입니다.\n✅ 방문객용은 'joonggonara-guest-5G'이며, 비밀번호는 'guest2023!@'입니다."
            },
            {
                "keywords": ["택배마감", "택배 마감", "택배시간", "택배 시간"],
                "answer": "✅ 사내 택배 마감 시간은 평일 오후 1시입니다. 주말에는 수거하지 않으니 참고해주세요."
            },
            {
                "keywords": ["근태 담당자", "근태담당자", "근태 문의"],
                "answer": "✅ Flex 근태, 휴가 관련 문의는 피플팀 이성헌님께 하시면 됩니다."
            }
        ]
        logger.info("주요 정보(Key Info) 설정 완료.")

    def setup_events(self):
        self.events = [
            {"name": "분기별 타운홀 미팅", "date": "2025-09-15", "details": "👥 전 직원 참여, 오후 2시 대회의실 🏢"},
            {"name": "연말 파티", "date": "2025-12-20", "details": "🎉 사내 연말 행사, 드레스 코드: 캐주얼"}
        ]
        logger.info("이벤트 설정 완료.")

    def split_text_into_chunks(self, text, max_length=500):
        chunks = []
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        
        for paragraph in paragraphs:
            if len(paragraph) <= max_length:
                chunks.append(paragraph)
            else:
                sentences = [s.strip() for s in paragraph.split('.') if s.strip()]
                current_chunk = ""
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) + 1 <= max_length:
                        current_chunk += sentence + "."
                    else:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = sentence + "."
                if current_chunk:
                    chunks.append(current_chunk.strip())
        
        return [chunk for chunk in chunks if len(chunk) > 30]

    def is_question_pattern(self, text):
        question_keywords = ["어떻게", "방법", "알려줘", "뭐야", "언제", "어디서", "누구", "연차", "회의실", "택배", "복리후생", "궁금"]
        return any(keyword in text.lower() for keyword in question_keywords)

    def detect_and_translate_language(self, text):
        try:
            detected = self.translator.detect(text)
            if detected.lang != 'ko' and detected.lang != 'en':
                translated_text = self.translator.translate(text, dest='ko').text
                logger.info(f"'{detected.lang}' -> 'ko'로 번역됨. 원본: '{text[:20]}...', 번역: '{translated_text[:20]}...'")
                return translated_text
            return text
        except Exception as e:
            logger.error(f"언어 감지 또는 번역 실패: {e}", exc_info=True)
            return text

    def search_knowledge(self, query, n_results=3):
        processed_query = self.detect_and_translate_language(query)
        for wrong, correct in self.ocr_fixes.items():
            processed_query = processed_query.replace(wrong, correct)
        
        for info in self.key_info:
            for keyword in info["keywords"]:
                if keyword in processed_query:
                    logger.info(f"주요 정보에서 일치하는 키워드({keyword}) 발견.")
                    return [info["answer"]], "key_info"

        for faq_question, faq_answer in self.faq.items():
            if faq_question.lower() in processed_query.lower():
                logger.info(f"FAQ에서 일치하는 질문({faq_question}) 발견.")
                return [faq_answer], "faq"

        if self.use_gemini:
            try:
                context_docs = self.collection.query(
                    query_embeddings=self.embedding_model.encode([processed_query]).tolist(),
                    n_results=n_results
                )
                context = "\n".join(context_docs['documents'][0]) if context_docs['documents'] else ""
                
                prompt = self.gemini_prompt_template.format(query=processed_query, context=context)
                gemini_response = self.gemini_model.generate_content(prompt)
                
                if gemini_response and hasattr(gemini_response, 'text'):
                    logger.info(f"Gemini API 응답 성공. 쿼리: {processed_query[:50]}...")
                    return [gemini_response.text], "gemini"
                else:
                    logger.warning(f"Gemini API 응답이 유효하지 않습니다. 폴백 검색 시도. 응답: {gemini_response}")
            except Exception as e:
                logger.error(f"Gemini API 호출 실패: {e}. 폴백 검색 시도.", exc_info=True)
        
        query_embedding = self.embedding_model.encode([processed_query])
        results = self.collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=n_results
        )
        logger.info(f"ChromaDB 검색 완료. 쿼리: {processed_query[:50]}...")
        return results['documents'][0] if results['documents'] else [], "chroma"

    def generate_response(self, query, relevant_data, response_type, user_id, channel_id):
        greeting = _get_session_greeting(self, user_id, channel_id)
        
        response_text = ""
        final_response_type = response_type

        if response_type == "key_info":
            response_text = relevant_data[0]
            response = f"{greeting}{response_text}\n더 궁금한 점이 있으시면 말씀해주세요."
        elif response_type == "faq":
            response_text = relevant_data[0]
            response = f"{greeting}{random.choice(self.responses['found'])}\n{response_text}\n더 궁금한 점이 있으시면 말씀해주세요. 💡"
        elif response_type == "gemini":
            response_text = relevant_data[0]
            response = f"{greeting}{response_text}"
        else: # chroma
            if not relevant_data:
                final_response_type = "not_found"
                response_text = random.choice(self.responses['not_found'])
                response = f"{greeting}{response_text}\n피플팀 담당자에게 문의해보시는 걸 추천드립니다. 📞"
            else:
                context = "\n".join(relevant_data[:2])
                response_text = f"{random.choice(self.responses['found'])}\n{context}"
                response = f"{greeting}{response_text}\n더 궁금한 점이 있으시면 말씀해주세요. 💡"

        return f"{response}\n{random.choice(self.responses['signature'])}", final_response_type

    def log_question(self, query, response_text, response_type):
        self.question_log.append({
            "query": query,
            "response": response_text,
            "response_type": response_type,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "personality": self.current_personality
        })
        logger.info(f"질문 로그 기록: 쿼리='{query[:50]}...', 응답 타입='{response_type}'")

# --- 봇 인스턴스 생성 ---
bot = PeopleAIBot()

# --- Slack 이벤트 핸들러 ---
@app.message(".*")
def handle_message(message, say):
    try:
        user_query = message['text']
        channel_id = message['channel']
        user_id = message['user']
        
        if message.get('user') == bot.bot_id:
            return

        auto_respond_channels_env = os.environ.get("AUTO_RESPOND_CHANNELS", "")
        auto_respond_channels = [c.strip() for c in auto_respond_channels_env.split(',') if c.strip()]
        
        if (bot.bot_id and f"<@{bot.bot_id}>" in user_query or
            message.get('channel_type') == 'im' or
            (channel_id in auto_respond_channels and bot.is_question_pattern(user_query))):
            
            clean_query = user_query.replace(f"<@{bot.bot_id}>", "").strip() if bot.bot_id else user_query.strip()
            
            if not clean_query or len(clean_query) < 2:
                logger.info(f"너무 짧거나 빈 쿼리 무시됨. 쿼리: '{clean_query}'")
                return
            
            relevant_data, response_type = bot.search_knowledge(clean_query)
            response, final_response_type = bot.generate_response(clean_query, relevant_data, response_type, user_id, channel_id)
            say(response)
            bot.log_question(clean_query, response, final_response_type)
            
    except Exception as e:
        logger.error(f"메시지 처리 실패: {e}", exc_info=True)
        say(f"문제가 생겼어요. ⚠️\n잠시 후 다시 시도해주세요.\n{random.choice(bot.responses['signature'])}")

@app.message("피플AI 도움말")
def handle_help(message, say):
    user_id = message['user']
    channel_id = message['channel']
    greeting_prefix = _get_session_greeting(bot, user_id, channel_id)

    help_text = """도움말을 알려드릴게요. ✨
피플AI는 중고나라 직원들의 회사생활을 돕는 AI입니다.
회사 정책, 복지, 절차 등을 질문하시면 빠르게 답변드립니다.

📋 사용 예시:
- `@피플AI 연차 신청 방법`
- `#people-team-help` 채널에서: `택배 발송 절차는?`
- DM으로: `How to book a meeting room?`

📝 명령어:
- `피플AI 모드변경`: 성격 변경 (프로/친구/해피)
- `피플AI 오늘의팁`: 회사생활 팁
- `피플AI 맛집추천`: 회사 근처 맛집
- `피플AI 이벤트`: 사내 이벤트 확인

더 궁금한 점이 있으시면 말씀해주세요.
"""
    response = greeting_prefix + help_text + random.choice(bot.responses['signature'])
    say(response)

@app.message("피플AI 모드변경")
def change_mode(message, say):
    user_id = message['user']
    channel_id = message['channel']
    greeting_prefix = _get_session_greeting(bot, user_id, channel_id)

    personalities = list(bot.personalities.keys())
    current_index = personalities.index(bot.current_personality)
    next_index = (current_index + 1) % len(personalities)
    bot.current_personality = personalities[next_index]
    
    new_mode_name = bot.personalities[bot.current_personality]['name']
    
    response_text = f"모드 변경이 완료되었습니다. ✅\n현재 모드는 {new_mode_name}입니다.\n어떤 도움을 드릴까요?\n"
    response = greeting_prefix + response_text + random.choice(bot.responses['signature'])
    say(response)

@app.message("피플AI 오늘의팁")
def daily_tip(message, say):
    user_id = message['user']
    channel_id = message['channel']
    greeting_prefix = _get_session_greeting(bot, user_id, channel_id)

    tips = [
        "💡 이메일 제목은 명확히 작성하세요.\n예시: '회의' 대신 '3/15 마케팅 회의'로!",
        "⏰ 회의 5분 전 입장하면 인상 좋아요.",
        "💰 사내 식당 무료 뷔페를 꼭 이용하세요. 점심 식비 절약에 최고! 😋"
    ]
    
    tip = random.choice(tips)
    response_text = f"오늘의 팁을 드릴게요. 📋\n{tip}\n더 궁금한 점이 있으시면 말씀해주세요.\n"
    response = greeting_prefix + response_text + random.choice(bot.responses['signature'])
    say(response)

@app.message("피플AI 맛집추천")
def recommend_restaurant(message, say):
    user_id = message['user']
    channel_id = message['channel']
    greeting_prefix = _get_session_greeting(bot, user_id, channel_id)

    restaurants = [
        "🍜 라멘집: 돈코츠 라멘 맛집 (도보 5분)",
        "🍕 피자스쿨: 점심 특가 피자 (도보 3분)",
        "🍱 한솥도시락: 간편한 도시락 (도보 2분)",
        "☕ 스타벅스: 회의하기 좋은 카페 (도보 1분)"
    ]
    
    recommended = '\n'.join(random.sample(restaurants, 2))
    response_text = f"중고나라 근처 맛집을 추천드립니다. 🏢\n{recommended}\n더 궁금한 점이 있으시면 말씀해주세요.\n"
    response = greeting_prefix + response_text + random.choice(bot.responses['signature'])
    say(response)

@app.message("피플AI 이벤트")
def events(message, say):
    user_id = message['user']
    channel_id = message['channel']
    greeting_prefix = _get_session_greeting(bot, user_id, channel_id)

    today = datetime.now()
    upcoming = [e for e in bot.events if datetime.strptime(e['date'], "%Y-%m-%d") >= today]

    if upcoming:
        event_list = [f"- {e['name']} ({e['date']}): {e['details']}" for e in upcoming]
        event_list_str = '\n'.join(event_list)
        response_text = f"다가오는 이벤트를 알려드립니다. 📅\n이벤트 목록:\n{event_list_str}\n더 궁금한 점이 있으시면 말씀해주세요.\n"
    else:
        response_text = """현재 예정된 이벤트는 없습니다. 😔
새로운 이벤트가 생기면 빠르게 알려드릴게요!
더 궁금한 점이 있으시면 말씀해주세요.
"""
    response = greeting_prefix + response_text + random.choice(bot.responses['signature'])
    say(response)

# --- Flask 라우팅 ---
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check():
    return "피플AI 정상 작동중! 🟢"

# --- 앱 실행 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    logger.info(f"Flask 앱을 포트 {port}에서 실행합니다.")
    flask_app.run(host="0.0.0.0", port=port)
