---
name: vibe-framework-crewai-tool
description: >
  Vibe Framework의 CrewAI Tool 작성 규칙.
  새 Tool 추가 시 반드시 참조. BaseTool 상속 없이 Tool을 작성하는 것은 금지.
---

# CrewAI Tool Skill

## Tool 위치 규칙

```
재사용 가능한 공통 Tool  → crewai/tools/
특정 Crew 전용 Tool      → crewai/crews/{crew_name}/tools.py
```

특정 Crew에서만 쓰이던 Tool이 2개 이상의 Crew에서 사용되면
반드시 `crewai/tools/` 로 이동한다.

---

## 공통 Tool 목록

```
crewai/tools/
├── video_tools.py      # FFmpeg, OpenCV 기반 영상 처리
├── storage_tools.py    # MinIO 업로드/다운로드
├── llm_tools.py        # LLM 직접 호출 (CrewAI 외부)
├── whisper_tools.py    # 음성 → 텍스트
└── db_tools.py         # PostgreSQL 조회 (읽기 전용)
```

---

## Tool 작성 표준 패턴

```python
# crewai/tools/storage_tools.py
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Optional
import boto3
import logging

logger = logging.getLogger(__name__)

# 입력 스키마 — 반드시 Pydantic으로 정의
class MinIODownloadInput(BaseModel):
    bucket: str = Field(description="MinIO 버킷명")
    object_key: str = Field(description="다운로드할 오브젝트 경로")
    local_path: str = Field(description="저장할 로컬 경로")

class MinIODownloadTool(BaseTool):
    name: str = "minio_download"
    description: str = (
        "MinIO에서 파일을 다운로드한다. "
        "영상, 이미지 등 처리가 필요한 파일을 가져올 때 사용."
    )
    args_schema: type[BaseModel] = MinIODownloadInput

    def _run(self, bucket: str, object_key: str, local_path: str) -> str:
        """동기 실행. CrewAI는 기본적으로 동기 Tool을 사용."""
        try:
            client = self._get_client()
            client.download_file(bucket, object_key, local_path)
            logger.info(f"Downloaded {bucket}/{object_key} → {local_path}")
            return f"success: {local_path}"
        except Exception as e:
            logger.error(f"MinIO download failed: {e}")
            return f"error: {str(e)}"

    def _get_client(self):
        import os
        return boto3.client(
            's3',
            endpoint_url=f"http://{os.getenv('MINIO_ENDPOINT')}",
            aws_access_key_id=os.getenv('MINIO_ROOT_USER'),
            aws_secret_access_key=os.getenv('MINIO_ROOT_PASSWORD'),
        )


class MinIOUploadInput(BaseModel):
    local_path: str = Field(description="업로드할 로컬 파일 경로")
    bucket: str = Field(description="MinIO 버킷명")
    object_key: str = Field(description="저장할 오브젝트 경로")

class MinIOUploadTool(BaseTool):
    name: str = "minio_upload"
    description: str = (
        "파일을 MinIO에 업로드한다. "
        "처리 완료된 영상, 중간 산출물 저장 시 사용. "
        "모든 산출물은 반드시 이 Tool로 저장해야 함."
    )
    args_schema: type[BaseModel] = MinIOUploadInput

    def _run(self, local_path: str, bucket: str, object_key: str) -> str:
        try:
            client = self._get_client()
            client.upload_file(local_path, bucket, object_key)
            return f"success: s3://{bucket}/{object_key}"
        except Exception as e:
            return f"error: {str(e)}"
```

---

## 영상 처리 Tool 패턴

```python
# crewai/tools/video_tools.py
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
import subprocess
import json

class ExtractClipsInput(BaseModel):
    video_path: str = Field(description="원본 영상 로컬 경로")
    timestamps: list[dict] = Field(description="[{start: 0.0, end: 5.0, label: '...'}]")
    output_dir: str = Field(description="클립 저장 디렉토리")

class ExtractClipsTool(BaseTool):
    name: str = "extract_video_clips"
    description: str = "타임스탬프 기반으로 영상에서 클립을 추출한다."
    args_schema: type[BaseModel] = ExtractClipsInput

    def _run(self, video_path: str, timestamps: list, output_dir: str) -> str:
        clips = []
        for i, ts in enumerate(timestamps):
            output_path = f"{output_dir}/clip_{i:03d}.mp4"
            cmd = [
                "ffmpeg", "-i", video_path,
                "-ss", str(ts["start"]),
                "-to", str(ts["end"]),
                "-c", "copy",
                output_path, "-y"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                clips.append(output_path)
            else:
                return f"error: FFmpeg failed — {result.stderr}"

        return json.dumps({"clips": clips, "count": len(clips)})
```

---

## Tool 작성 금지 패턴

```python
# ❌ args_schema 없이 작성 — Agent가 파라미터를 모름
class BadTool(BaseTool):
    name = "bad_tool"
    description = "뭔가 하는 툴"
    def _run(self, input: str):  # 타입 정보 없음
        pass

# ❌ Tool 안에서 다른 Agent 호출
class BadTool(BaseTool):
    def _run(self, ...):
        other_agent = SomeAgent()  # 절대 금지

# ❌ Tool 안에서 직접 DB 쓰기
class BadTool(BaseTool):
    def _run(self, ...):
        db.execute("INSERT INTO ...")  # DB 쓰기는 BaseCrew에서만

# ✅ Tool은 단일 책임 — 읽기/처리/저장 각각 분리된 Tool로
```

---

## Tool 에러 반환 규칙

Tool은 예외를 raise하지 않고 **문자열로 에러를 반환**한다.
Agent가 에러 메시지를 읽고 재시도 또는 다른 전략을 선택할 수 있도록.

```python
def _run(self, ...) -> str:
    try:
        # 처리
        return f"success: {result}"
    except FileNotFoundError as e:
        return f"error:file_not_found: {str(e)}"
    except Exception as e:
        return f"error:unknown: {str(e)}"
```

에러 접두사 규칙:
- `success:` — 성공
- `error:file_not_found:` — 파일 없음
- `error:permission:` — 권한 없음
- `error:timeout:` — 타임아웃
- `error:unknown:` — 알 수 없는 에러
