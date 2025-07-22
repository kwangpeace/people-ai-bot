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

        self.gemini_prompt_template = """
당신은 '중고나라'의 친절한 AI 동료 '피플AI'입니다. 당신의 임무는 제공된 '참고 자료'만을 사용하여 동료의 질문에 답변하는 것입니다.

**핵심 규칙:**
1.  **자료 기반 답변:** 답변은 반드시 '참고 자료' 내용에만 근거해야 합니다. 자료에 없는 내용은 절대로 추측하거나 외부 지식을 사용해 답변하지 마세요.
2.  **슬랙 형식 준수:**
    -   핵심 답변을 2~3줄로 먼저 제시하세요.
    -   모든 문장("~다.", "~요." 등) 끝에는 반드시 줄바꿈을 추가하여 가독성을 높여주세요.
    -   항목을 나열할 때는 글머리 기호(-)나 번호 매기기(1., 2.)를 사용하세요.
    -   텍스트를 굵게(**) 만들지 마세요.
3.  **모를 경우:** 참고 자료에서 명확한 답을 찾을 수 없다면, "음, 문의주신 부분에 대해서는 제가 지금 바로 명확한 답변을 드리기는 조금 어렵네요." 와 같이 부드럽게 말하고 피플팀 문의를 안내하세요.

**대화 시작:**
-   대화가 처음 시작될 때만 "안녕하세요!" 같은 인사를 사용하세요.

질문: {query}
참고 자료:
---
{context}
---
"""
        self.setup_chroma_db()
        self.setup_personalities()
        self.setup_responses()
        self.setup_ocr_fixes()
        self.setup_key_info()
        self.setup_events()
        
        # ChromaDB 초기화 및 데이터 로딩
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
            "searching": [
                "생각하는 중입니다... 🤔",
                "잠시만 기다려주세요. 피플AI가 열심히 답을 찾고 있어요! 🏃‍♂️",
                "데이터를 분석하고 있어요. 곧 답변해 드릴게요! 📊",
                "가이드북을 샅샅이 뒤지는 중... 📚"
            ],
            "not_found": ["음, 문의주신 부분은 제가 지금 명확히 답변드리기 어렵네요. ⚠️", "제가 아는 선에서는 해당 정보가 확인되지 않아요. ❌"]
        }
        logger.info("응답 메시지 설정 완료.")

    def setup_ocr_fixes(self):
        self.ocr_fixes = {
            "연치": "연차", "복리후셍": "복리후생", "회으실": "회의실",
            "택배실": "택배실", "결제": "결재", "급여명세서": "급여명세서"
        }
        logger.info("OCR 수정 맵 설정 완료.")
    
    def setup_key_info(self):
        """AI가 놓치기 쉬운 핵심 정보를 키워드 기반으로 설정합니다."""
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
            },
            {
                "keywords": ["맛집", "밥집", "점심", "저녁"],
                "answer": "✅ 중고나라 본사 근처 맛집 정보는 가이드 문서에 정리되어 있어요. '주변 맛집 리스트'라고 물어보시면 더 자세히 알려드릴게요!"
            },
            {
                "keywords": ["웹사이트", "홈페이지", "블로그"],
                "answer": "✅ 중고나라 공식 웹사이트 주소는 다음과 같습니다:\n- 중고나라 서비스: https://www.joongna.com/\n- 중고나라 기술 블로그: https://teamblog.joonggonara.co.kr/"
            }
        ]
        logger.info("주요 정보(Key Info) 설정 완료.")

    def setup_events(self):
        self.events = [
            {"name": "분기별 타운홀 미팅", "date": "2025-09-15", "details": "👥 전 직원 참여, 오후 2시 대회의실 🏢"},
            {"name": "연말 파티", "date": "2025-12-20", "details": "🎉 사내 연말 행사, 드레스 코드: 캐주얼"}
        ]
        logger.info("이벤트 설정 완료.")

    def split_text_into_chunks(self, text, max_length=1000, overlap=100):
        """의미 단위(문단)를 유지하며 텍스트를 청크로 나눕니다."""
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        
        chunks = []
        for paragraph in paragraphs:
            if len(paragraph) <= max_length:
                chunks.append(paragraph)
            else:
                sentences = [s.strip() for s in paragraph.split('.') if s.strip()]
                current_chunk = ""
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) + 1 <= max_length:
                        current_chunk += sentence + ". "
                    else:
                        chunks.append(current_chunk.strip())
                        current_chunk = current_chunk[-overlap:] + sentence + ". "
                if current_chunk:
                    chunks.append(current_chunk.strip())
        
        return [chunk for chunk in chunks if len(chunk) > 50]

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

    def search_knowledge(self, query, n_results=5):
        """사용자 질문에 대해 키워드 검색 후, AI 검색을 수행합니다."""
        processed_query = self.detect_and_translate_language(query)
        for wrong, correct in self.ocr_fixes.items():
            processed_query = processed_query.replace(wrong, correct)
        
        # 1. Key Info (키워드) 검색
        for info in self.key_info:
            for keyword in info["keywords"]:
                if keyword in processed_query:
                    logger.info(f"주요 정보에서 일치하는 키워드({keyword}) 발견.")
                    return [info["answer"]], "key_info"

        # 2. RAG (ChromaDB + Gemini)
        try:
            context_docs = self.collection.query(
                query_embeddings=self.embedding_model.encode([processed_query]).tolist(),
                n_results=n_results
            )
            context = "\n\n".join(context_docs['documents'][0]) if context_docs and context_docs['documents'] else ""
            logger.info(f"ChromaDB 검색 완료. 쿼리: {processed_query[:50]}... {n_results}개 결과 사용.")
        except Exception as e:
            logger.error(f"ChromaDB 검색 실패: {e}", exc_info=True)
            context = ""

        if self.use_gemini and context:
            try:
                prompt = self.gemini_prompt_template.format(query=processed_query, context=context)
                gemini_response = self.gemini_model.generate_content(prompt)
                
                if gemini_response and hasattr(gemini_response, 'text') and gemini_response.text:
                    logger.info(f"Gemini API 응답 성공. 쿼리: {processed_query[:50]}...")
                    return [gemini_response.text], "gemini"
                else:
                    logger.warning(f"Gemini API 응답이 비어있거나 유효하지 않습니다. 응답: {gemini_response}")
            except Exception as e:
                logger.error(f"Gemini API 호출 실패: {e}", exc_info=True)
        
        return [], "not_found"

    def generate_response(self, query, relevant_data, response_type, user_id, channel_id):
        greeting = _get_session_greeting(self, user_id, channel_id)
        
        if response_type == "key_info":
            response_text = relevant_data[0]
            response = f"{greeting}{response_text}"
        elif response_type == "gemini":
            response_text = relevant_data[0]
            response = f"{greeting}{response_text}"
        else: # not_found
            response_text = random.choice(self.responses['not_found'])
            response = f"{greeting}{response_text}\n피플팀 담당자에게 문의해보시는 건 어떨까요? 📞"

        return response, response_type

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
            
            say(random.choice(bot.responses['searching']))
            
            relevant_data, response_type = bot.search_knowledge(clean_query)
            response, final_response_type = bot.generate_response(clean_query, relevant_data, response_type, user_id, channel_id)
            say(response)
            bot.log_question(clean_query, response, final_response_type)
            
    except Exception as e:
        logger.error(f"메시지 처리 실패: {e}", exc_info=True)
        say(f"문제가 생겼어요. ⚠️\n잠시 후 다시 시도해주세요.")

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
    response = greeting_prefix + help_text
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
    response = greeting_prefix + response_text
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
    response = greeting_prefix + response_text
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
    response = greeting_prefix + response_text
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
    response = greeting_prefix + response_text
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
