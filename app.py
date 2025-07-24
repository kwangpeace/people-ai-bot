import os
import random
import logging
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
import chromadb
from sentence_transformers import SentenceTransformer
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
        # 봇 ID 가져오기
        try:
            self.bot_id = app.client.auth_test()['user_id']
            logger.info(f"봇 ID({self.bot_id})를 성공적으로 가져왔습니다.")
        except Exception as e:
            logger.error(f"봇 ID 가져오기 실패: {e}")
            self.bot_id = None

        # Gemini API 설정
        self.gemini_model = self.setup_gemini()

        # ChromaDB 및 임베딩 모델 설정
        self.collection, self.embedding_model = self.setup_chroma_db()

        # 지식 데이터 로드
        self.load_knowledge_data()
        
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
            # 원하시는 모델 이름으로 설정하세요.
            model = genai.GenerativeModel("gemini-2.0-flash") 
            logger.info("Gemini API 활성화 완료.")
            return model
        except Exception as e:
            logger.error(f"Gemini 모델 설정 실패: {e}")
            return None

    def setup_chroma_db(self):
        try:
            db_path = "./chroma_db"
            chroma_client = chromadb.PersistentClient(path=db_path)
            collection = chroma_client.get_or_create_collection(name="junggonara_guide")
            embedding_model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
            logger.info("ChromaDB 및 SentenceTransformer 설정 완료.")
            return collection, embedding_model
        except Exception as e:
            logger.critical(f"ChromaDB 또는 임베딩 모델 설정 실패: {e}")
            return None, None
            
    def load_knowledge_data(self):
        if not self.collection or not self.embedding_model:
            logger.error("DB/모델이 초기화되지 않아 데이터 로드를 건너뜁니다.")
            return
            
        if self.collection.count() > 0:
            logger.info("ChromaDB에 이미 데이터가 존재하여 로드를 건너뜁니다.")
            return
            
        try:
            with open("guide_data.txt", 'r', encoding='utf-8') as f:
                text = f.read()
            
            # '---' 기준으로 텍스트를 분할하여 의미 단위의 청크 생성
            text_chunks = [chunk.strip() for chunk in text.split('---') if chunk.strip()]

            if not text_chunks:
                logger.warning("guide_data.txt 파일이 비어있거나 유효한 청크가 없습니다.")
                return

            embeddings = self.embedding_model.encode(text_chunks)
            ids = [f"chunk_{i}" for i in range(len(text_chunks))]
            self.collection.add(documents=text_chunks, embeddings=embeddings.tolist(), ids=ids)
            logger.info(f"지식 데이터 로드 완료: {len(text_chunks)}개 청크가 DB에 추가되었습니다.")

        except FileNotFoundError:
            logger.error("'guide_data.txt'을 찾을 수 없습니다.")
        except Exception as e:
            logger.error(f"지식 데이터 로드 중 오류 발생: {e}", exc_info=True)

    def search_knowledge(self, query, n_results=3):
        if not self.collection: return ""
        try:
            results = self.collection.query(query_texts=[query], n_results=n_results)
            context = "\n\n---\n\n".join(results['documents'][0]) if results and results['documents'] else ""
            logger.info(f"'{query}'에 대한 지식 검색 완료. {len(results['documents'][0])}개의 관련 청크를 찾았습니다.")
            logger.debug(f"검색된 컨텍스트:\n{context}")
            return context
        except Exception as e:
            logger.error(f"ChromaDB 검색 실패: {e}", exc_info=True)
            return ""

    def generate_final_answer(self, query, context):
        if not self.gemini_model:
            return "AI 모델이 설정되지 않아 답변을 생성할 수 없습니다."
        
        prompt = f"""
[지시문]
당신은 '중고나라' 회사의 규정과 주변 정보를 정확하게 안내하는 AI 어시스턴트 '피플AI'입니다. 당신의 유일한 임무는 아래 제공된 '[참고 자료]'의 내용만을 기반으로 사용자의 질문에 답변하는 것입니다.

[엄격한 작업 절차]
1. 사용자의 '[질문]'을 주의 깊게 읽고, 질문에 '평점', '가까운', '종류' 등 **조건이나 필터링**이 포함되어 있는지 파악합니다.
2. '[참고 자료]'에서 질문과 관련된 정보 블록을 모두 찾습니다.
3. 만약 질문에 조건이 포함되어 있다면, 찾은 정보 블록 내의 구조화된 데이터(예: '네이버 평점: 4.4', '거리: 약 200m')를 보고 **조건에 맞는 정보만 선별합니다.**
   (예: "평점 4.5 이상인 곳"을 물으면, '네이버 평점' 항목이 4.5 이상인 식당만 골라냅니다.)
4. 선별된 정보를 바탕으로 사용자가 보기 쉽게 목록 형태로 답변을 생성합니다.
5. 만약 문서에서 질문에 대한 내용을 찾을 수 없다면, **오직 "문의주신 내용은 제가 가진 정보에서는 찾기 어렵네요. 피플팀에 직접 문의해주시겠어요? 📞"** 라고만 답변해야 합니다.

[답변 형식 규칙]
- 한 줄에 한 문장만 작성하여 가독성을 높입니다.
- 굵은 글씨(**) 같은 텍스트 강조는 절대 사용하지 않습니다.

---
[참고 자료]
{context}
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

# 1. 봇이 채널에서 멘션되었을 때만 반응하는 핸들러
@app.event("app_mention")
def handle_app_mention_events(body, say, logger):
    logger.info("app_mention 이벤트를 수신했습니다. (채널 호출)")
    try:
        user_query = body["event"]["text"]
        user_id = body["event"]["user"]
        channel_id = body["event"]["channel"]
        thread_ts = body["event"].get("ts") # 스레드에 답변을 달기 위해 ts를 가져옴

        # 멘션 부분(<@BOT_ID>)을 쿼리에서 제거
        clean_query = user_query.replace(f"<@{bot.bot_id}>", "").strip()

        if not clean_query or len(clean_query) < 2:
            say(text="무엇이 궁금하신가요? 좀 더 구체적으로 질문해주세요. 😊", thread_ts=thread_ts)
            return

        # 채널에서는 스레드로 답변하여 깔끔하게 유지
        thinking_message = say(text=random.choice(bot.responses['searching']), thread_ts=thread_ts)

        context = bot.search_knowledge(clean_query)
        final_answer = bot.generate_final_answer(clean_query, context)

        app.client.chat_update(
            channel=channel_id,
            ts=thinking_message['ts'],
            text=final_answer
        )

    except Exception as e:
        logger.error(f"app_mention 이벤트 처리 실패: {e}", exc_info=True)
        say(text=f"앗, 예상치 못한 오류가 발생했어요. 😢", thread_ts=body["event"].get("ts"))


# 2. DM(개인 메시지)에만 반응하는 핸들러
@app.event("message")
def handle_message_events(body, say, logger):
    # 이벤트가 DM 채널에서 발생했는지 확인
    if body["event"].get("channel_type") == "im":
        logger.info("DM 메시지 이벤트를 수신했습니다.")
        try:
            user_query = body["event"]["text"]
            user_id = body["event"]["user"]
            channel_id = body["event"]["channel"]
            
            # 봇이 보낸 메시지나 채널 참여/퇴장 같은 시스템 메시지는 무시
            if "subtype" in body["event"] or (bot.bot_id and user_id == bot.bot_id):
                return

            clean_query = user_query.strip()

            if not clean_query or len(clean_query) < 2:
                say("무엇이 궁금하신가요? 좀 더 구체적으로 질문해주세요. 😊")
                return
            
            thinking_message = say(random.choice(bot.responses['searching']))
            context = bot.search_knowledge(clean_query)
            final_answer = bot.generate_final_answer(clean_query, context)

            app.client.chat_update(
                channel=channel_id,
                ts=thinking_message['ts'],
                text=final_answer
            )
        except Exception as e:
            logger.error(f"DM 메시지 처리 실패: {e}", exc_info=True)
            say("앗, 예상치 못한 오류가 발생했어요. 😢")

# --- Flask 라우팅 ---
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health_check():
    return "피플AI (벡터 검색 모드) 정상 작동중! 🟢"

# --- 앱 실행 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
