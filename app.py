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
        
        # *** DB 자동 업데이트 로직 ***
        logger.info("DB 자동 업데이트를 위해 기존 ChromaDB 컬렉션을 삭제합니다.")
        try:
            self.chroma_client.delete_collection(name="junggonara_guide")
            logger.info("기존 ChromaDB 컬렉션을 성공적으로 삭제했습니다.")
        except Exception as e:
            logger.warning(f"기존 ChromaDB 컬렉션 삭제 중 오류 발생 (초기 실행 시 정상): {e}")
        
        self.collection = self.chroma_client.get_or_create_collection(name="junggonara_guide")
        logger.info("최신 가이드 데이터로 ChromaDB를 새로 구축합니다.")
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
                logger.info(f"최신 데이터로 ChromaDB 구축 완료: {len(text_chunks)}개 청크 추가됨.")
            else:
                logger.warning("가이드 텍스트 파일에서 유효한 텍스트 청크를 추출하지 못했습니다.")
        else:
            logger.error("가이드 텍스트 파일을 읽지 못해 DB를 구축할 수 없습니다.")

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
        logger.info("OCR
