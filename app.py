import os
import random
import logging
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
import chromadb
from sentence_transformers import SentenceTransformer
from datetime import datetime
import google.generativeai as genai

# --- 로깅 설정 (표준 출력으로 변경하여 Railway 로그에서 확인 용이) ---
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
    # 앱 초기화 실패 시 실행을 중단해야 할 수 있음
    exit()


# --- 메인 봇 클래스 ---
class PeopleAIBot:
    def __init__(self):
        self.bot_name = "피플AI"
        self.company_name = "중고나라"
        
        # 봇 ID 가져오기
        try:
            self.bot_id = app.client.auth_test()['user_id']
            logger.info(f"봇 ID({self.bot_id})를 성공적으로 가져왔습니다.")
        except Exception as e:
            logger.error(f"봇 ID를 가져오는 데 실패했습니다. SLACK_BOT_TOKEN을 확인하세요. 오류: {e}")
            self.bot_id = None

        # Gemini API 설정
        self.gemini_model = self.setup_gemini()

        # ChromaDB 및 임베딩 모델 설정
        self.collection, self.embedding_model = self.setup_chroma_db()

        # 데이터베이스에 지식 데이터 로드
        self.load_knowledge_data()
        
        # 기타 설정
        self.setup_bot_features()
        self.session_tracker = {}

    def setup_gemini(self):
        """Gemini API 클라이언트를 설정하고 모델을 반환합니다."""
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            logger.error("GEMINI_API_KEY 환경 변수가 설정되지 않았습니다. Gemini 기능이 비활성화됩니다.")
            return None
        try:
            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel("gemini-1.5-flash-latest") # 필요시 "gemini-2.0-flash"로 변경
            logger.info("Gemini API 활성화 완료.")
            return model
        except Exception as e:
            logger.error(f"Gemini 모델 설정 실패: {e}")
            return None

    def setup_chroma_db(self):
        """ChromaDB 클라이언트와 임베딩 모델을 설정하고 반환합니다."""
        try:
            db_path = "./chroma_db"  # 로컬 파일 시스템에 저장
            chroma_client = chromadb.PersistentClient(path=db_path)
            collection = chroma_client.get_or_create_collection(
                name="junggonara_guide",
                metadata={"description": "중고나라 회사 가이드 데이터"}
            )
            embedding_model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
            logger.info(f"ChromaDB({db_path}) 및 SentenceTransformer 설정 완료.")
            return collection, embedding_model
        except Exception as e:
            logger.critical(f"ChromaDB 또는 임베딩 모델 설정 실패: {e}")
            return None, None
            
    def load_knowledge_data(self):
        """guide_data.txt 파일에서 지식 데이터를 읽어 ChromaDB에 저장합니다."""
        if not self.collection or not self.embedding_model:
            logger.error("DB 또는 모델이 초기화되지 않아 데이터 로드를 건너뜁니다.")
            return

        # DB가 비어있을 때만 데이터를 새로 로드합니다.
        # DB를 강제로 새로고침하려면 서버에서 chroma_db 폴더를 삭제해야 합니다.
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

            # 데이터베이스에 청크 추가
            embeddings = self.embedding_model.encode(text_chunks)
            ids = [f"chunk_{i}" for i in range(len(text_chunks))]
            self.collection.add(
                documents=text_chunks,
                embeddings=embeddings.tolist(),
                ids=ids,
                metadatas=[{"source": "guide_data.txt"} for _ in text_chunks]
            )
            logger.info(f"지식 데이터 로드 완료: {len(text_chunks)}개 청크가 DB에 추가되었습니다.")

        except FileNotFoundError:
            logger.error("데이터 파일 'guide_data.txt'을 찾을 수 없습니다. 프로젝트 루트에 파일을 생성해주세요.")
        except Exception as e:
            logger.error(f"지식 데이터 로드 중 오류 발생: {e}", exc_info=True)

    def search_knowledge(self, query, n_results=3):
        """사용자 질문과 가장 관련 높은 지식 청크를 ChromaDB에서 검색합니다."""
        if not self.collection:
            return ""
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results
            )
            # 검색된 문서들을 하나의 컨텍스트로 결합
            context = "\n\n---\n\n".join(results['documents'][0]) if results['documents'] else ""
            logger.info(f"'{query}'에 대한 지식 검색 완료. {len(results['documents'][0])}개의 관련 청크를 찾았습니다.")
            # 디버깅을 위해 검색된 컨텍스트를 로그로 출력
            logger.debug(f"검색된 컨텍스트:\n{context}")
            return context
        except Exception as e:
            logger.error(f"ChromaDB 검색 실패: {e}", exc_info=True)
            return ""

    def generate_final_answer(self, query, context):
        """검색된 컨텍스트를 바탕으로 Gemini를 이용해 최종 답변을 생성합니다."""
        if not self.gemini_model:
            return "AI 모델이 설정되지 않아 답변을 생성할 수 없습니다. 관리자에게 문의해주세요."

        # Gemini에게 전달할 프롬프트
        prompt = f"""
[당신의 역할]
당신은 '중고나라' 회사의 피플팀 AI 어시스턴트 '피플AI'입니다. 동료 직원들에게 회사 생활 정보를 친절하고 정확하게 안내하는 것이 당신의 임무입니다.

[매우 중요한 규칙]
- **반드시** 아래 제공된 '[참고 자료]' 내용에만 근거해서 답변해야 합니다. 당신의 일반 지식이나 외부 정보를 절대 사용하지 마세요.
- 참고 자료에 질문과 관련된 내용이 전혀 없다면, "문의주신 내용은 제가 가진 정보에서는 찾기 어렵네요. 피플팀에 직접 문의해주시겠어요? 📞" 라고만 답변하세요.
- 슬랙(Slack) 가독성에 최적화된 형식으로 답변해주세요.
  1. 모든 문장 끝(~다, ~요 등)에는 줄바꿈을 넣어 한 줄에 한 문장만 표시합니다.
  2. 텍스트를 굵게 만드는 마크다운(**)은 절대 사용하지 마세요.
  3. 이모지는 정보 구분을 위해 제한적으로 사용하세요 (예: ✅, 📅, 💡, ⚠️). 감정 표현 이모지는 사용하지 마세요.

---
[참고 자료]
{context}
---

[직원의 질문]
{query}

[답변]
"""
        try:
            response = self.gemini_model.generate_content(prompt)
            logger.info(f"Gemini API 응답 생성 성공. (쿼리: {query[:30]}...)")
            return response.text
        except Exception as e:
            logger.error(f"Gemini API 호출 실패: {e}", exc_info=True)
            return "AI 답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."

    def setup_bot_features(self):
        """봇의 고정적인 응답, 성격 등을 설정합니다."""
        self.responses = {
            "searching": ["잠시만요, 관련 정보를 찾고 있어요... 🕵️‍♀️", "생각하는 중... 🤔", "데이터를 분석하고 있어요! 📊"]
        }
        # 다른 기능들(성격, 이벤트 등) 필요 시 여기에 추가
        logger.info("봇 기능 설정 완료.")


# --- 봇 인스턴스 생성 ---
bot = PeopleAIBot()

# --- Slack 이벤트 핸들러 ---
@app.message(".*")
def handle_message(message, say):
    try:
        user_query = message['text']
        user_id = message['user']
        channel_id = message['channel']

        # 봇 자신의 메시지는 무시
        if bot.bot_id and user_id == bot.bot_id:
            return

        # 봇을 멘션했거나, DM이거나, 특정 채널에서 질문 패턴이 감지될 때만 응답
        is_im = message.get('channel_type') == 'im'
        is_mentioned = bot.bot_id and f"<@{bot.bot_id}>" in user_query
        
        if is_im or is_mentioned:
            # 멘션 제거 후 순수 쿼리 추출
            clean_query = user_query.replace(f"<@{bot.bot_id}>", "").strip()
            
            if not clean_query or len(clean_query) < 2:
                say("무엇이 궁금하신가요? 좀 더 구체적으로 질문해주세요. 😊")
                return
            
            # 1. "생각 중" 메시지 먼저 전송
            thinking_message = say(random.choice(bot.responses['searching']))

            # 2. 지식 베이스에서 관련 정보 검색
            context = bot.search_knowledge(clean_query)
            
            # 3. 검색된 정보 바탕으로 최종 답변 생성
            final_answer = bot.generate_final_answer(clean_query, context)

            # 4. 슬랙에 최종 답변 전송 (기존 "생각 중" 메시지 수정)
            app.client.chat_update(
                channel=channel_id,
                ts=thinking_message['ts'],
                text=final_answer
            )
            
    except Exception as e:
        logger.error(f"메시지 처리 실패: {e}", exc_info=True)
        say(f"앗, 예상치 못한 오류가 발생했어요. 😢\n잠시 후 다시 시도해주세요.")

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
