---
name: vibe-framework-minio
description: >
  Vibe Framework의 MinIO 파일/영상 스토리지 사용 규칙.
  파일 업로드/다운로드, 버킷 구성, Python boto3 연동 코드 작성 시 반드시 참조.
  MinIO를 거치지 않고 파일을 메모리에 보관하는 코드는 절대 생성 금지.
---

# MinIO Skill

## 핵심 원칙

1. **모든 파일은 MinIO를 통해서만 공유** — 컨테이너 간 파일 직접 전달 절대 금지
2. **중간 산출물도 반드시 저장** — 처리 중 생성되는 모든 파일을 MinIO에 저장 (재시도/디버깅용)
3. **버킷은 용도별로 분리** — 하나의 버킷에 모든 파일 혼재 금지
4. **object key는 job_id 기반** — 파일 추적 및 정리가 용이하도록
5. **로컬 임시 파일은 처리 후 즉시 삭제** — 컨테이너 디스크 용량 관리

---

## 버킷 구성 표준

```yaml
# infra/minio/buckets.yml
buckets:
  - name: raw-uploads        # 사용자 원본 업로드
    retention_days: 7
    versioning: false

  - name: checkpoints        # Worker 중간 산출물 (재시도용)
    retention_days: 3
    versioning: false

  - name: processed-outputs  # 최종 완성 결과물
    retention_days: 30
    versioning: false

  - name: thumbnails         # 썸네일 이미지
    retention_days: 30
    versioning: false
```

---

## Object Key 네이밍 규칙

```
{버킷}/{job_id}/{단계}/{파일명}

예시:
raw-uploads/abc-123/original/video.mp4
checkpoints/abc-123/step-01-extracted/clip_001.mp4
checkpoints/abc-123/step-02-script/script.json
processed-outputs/abc-123/final/reels.mp4
thumbnails/abc-123/thumbnail.jpg
```

규칙:
- `{단계}` 는 `step-{번호}-{설명}` 형식
- 파일명은 소문자 + kebab-case
- UUID나 타임스탬프는 job_id로 대체 (중복 방지)

---

## Python 연동 표준 (boto3)

### MinIO 클라이언트 설정

```python
# core/minio.py — 모든 Python 서비스 공통
import boto3
from botocore.client import Config
import os
import logging
import structlog

logger = structlog.get_logger()

def get_minio_client():
    """MinIO S3 호환 클라이언트 반환."""
    return boto3.client(
        's3',
        endpoint_url=f"http://{os.getenv('MINIO_ENDPOINT', 'minio:9000')}",
        aws_access_key_id=os.getenv('MINIO_ROOT_USER'),
        aws_secret_access_key=os.getenv('MINIO_ROOT_PASSWORD'),
        config=Config(signature_version='s3v4'),
        region_name='us-east-1',  # MinIO는 region 무관, 형식만 맞추면 됨
    )

# 싱글톤 클라이언트 (서비스 시작 시 1회 생성)
_client = None

def minio_client():
    global _client
    if _client is None:
        _client = get_minio_client()
    return _client
```

### 업로드 표준 패턴

```python
# core/minio.py
import os
import structlog

logger = structlog.get_logger()

async def upload_file(
    local_path: str,
    bucket: str,
    object_key: str,
    job_id: str,
    content_type: str = "application/octet-stream",
) -> str:
    """
    파일을 MinIO에 업로드하고 object URL 반환.
    모든 업로드는 이 함수를 통해서만 수행.
    """
    client = minio_client()
    file_size = os.path.getsize(local_path)

    logger.info("minio_upload_start",
        job_id=job_id,
        bucket=bucket,
        object_key=object_key,
        file_size_mb=round(file_size / 1024 / 1024, 2),
    )

    try:
        client.upload_file(
            local_path,
            bucket,
            object_key,
            ExtraArgs={"ContentType": content_type},
        )
        url = f"s3://{bucket}/{object_key}"
        logger.info("minio_upload_complete",
            job_id=job_id,
            object_key=object_key,
            url=url,
        )
        return url

    except Exception as e:
        logger.error("minio_upload_failed",
            job_id=job_id,
            bucket=bucket,
            object_key=object_key,
            error=str(e),
            exc_info=True,
        )
        raise


async def download_file(
    bucket: str,
    object_key: str,
    local_path: str,
    job_id: str,
) -> str:
    """
    MinIO에서 파일을 다운로드하고 로컬 경로 반환.
    모든 다운로드는 이 함수를 통해서만 수행.
    """
    client = minio_client()
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    logger.info("minio_download_start",
        job_id=job_id,
        bucket=bucket,
        object_key=object_key,
    )

    try:
        client.download_file(bucket, object_key, local_path)
        file_size = os.path.getsize(local_path)
        logger.info("minio_download_complete",
            job_id=job_id,
            local_path=local_path,
            file_size_mb=round(file_size / 1024 / 1024, 2),
        )
        return local_path

    except Exception as e:
        logger.error("minio_download_failed",
            job_id=job_id,
            bucket=bucket,
            object_key=object_key,
            error=str(e),
            exc_info=True,
        )
        raise


def generate_presigned_url(
    bucket: str,
    object_key: str,
    expires_in: int = 3600,
) -> str:
    """
    Flutter에서 직접 다운로드할 수 있는 presigned URL 생성.
    완성된 결과물을 Flutter에 전달할 때 사용.
    """
    client = minio_client()
    return client.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket, 'Key': object_key},
        ExpiresIn=expires_in,
    )


def cleanup_local_file(local_path: str, job_id: str):
    """
    처리 완료 후 임시 파일 삭제.
    MinIO 업로드 완료 후 반드시 호출.
    """
    try:
        if os.path.exists(local_path):
            os.remove(local_path)
            logger.info("local_file_cleaned", job_id=job_id, path=local_path)
    except Exception as e:
        logger.warning("local_file_cleanup_failed",
            job_id=job_id,
            path=local_path,
            error=str(e),
        )
```

---

## Worker 파일 처리 표준 패턴

```python
# workers/{name}/processor.py
import tempfile
import os
from core.minio import download_file, upload_file, cleanup_local_file

async def process(event: dict) -> dict:
    job_id = event["job_id"]

    # 임시 디렉토리 사용 (컨테이너 재시작 시 자동 정리)
    with tempfile.TemporaryDirectory() as tmp_dir:

        # 1. 원본 다운로드
        local_video = os.path.join(tmp_dir, "input.mp4")
        await download_file(
            bucket="raw-uploads",
            object_key=event["video_key"],
            local_path=local_video,
            job_id=job_id,
        )

        # 2. 처리
        output_path = os.path.join(tmp_dir, "output.mp4")
        await run_processing(local_video, output_path)

        # 3. 중간 산출물 저장 (실패 시 재시도 가능하도록)
        checkpoint_key = f"{job_id}/step-01-processed/output.mp4"
        await upload_file(
            local_path=output_path,
            bucket="checkpoints",
            object_key=checkpoint_key,
            job_id=job_id,
            content_type="video/mp4",
        )

        # 4. 최종 결과 저장
        final_key = f"{job_id}/final/reels.mp4"
        result_url = await upload_file(
            local_path=output_path,
            bucket="processed-outputs",
            object_key=final_key,
            job_id=job_id,
            content_type="video/mp4",
        )

    # tmp_dir 컨텍스트 종료 시 자동 삭제
    return {"result_url": result_url, "object_key": final_key}
```

---

## FastAPI 대용량 파일 업로드 패턴

```python
# backend/routers/{feature}.py
import tempfile
import os
from fastapi import UploadFile
from core.minio import upload_file
import structlog

logger = structlog.get_logger()

async def handle_upload(video: UploadFile, job_id: str) -> str:
    """
    스트리밍 업로드 — 전체를 메모리에 올리지 않음.
    """
    object_key = f"{job_id}/original/{video.filename}"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
        tmp_path = tmp.name
        # 청크 단위로 읽어서 임시 파일에 저장
        chunk_size = 1024 * 1024  # 1MB
        while chunk := await video.read(chunk_size):
            tmp.write(chunk)

    try:
        url = await upload_file(
            local_path=tmp_path,
            bucket="raw-uploads",
            object_key=object_key,
            job_id=job_id,
            content_type=video.content_type,
        )
        return object_key
    finally:
        os.unlink(tmp_path)  # 임시 파일 반드시 삭제
```

---

## Flutter에서 결과 파일 수신 패턴

```
완성된 파일은 presigned URL로 Flutter에 전달.
Flutter가 직접 MinIO에 접근하지 않음.

흐름:
Worker 완료
  → PostgreSQL job.result_key 업데이트
  → Supabase Realtime으로 Flutter에 completed 이벤트 푸시
  → Flutter가 GET /jobs/{job_id}/result 호출
  → FastAPI가 presigned URL 생성 후 반환
  → Flutter가 presigned URL로 영상 다운로드
```

```python
# backend/routers/jobs.py
@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str, db = Depends(get_db)):
    job = await get_job(db, job_id)
    if job.status != "completed":
        raise HTTPException(400, "Job not completed yet")

    presigned_url = generate_presigned_url(
        bucket="processed-outputs",
        object_key=job.result_key,
        expires_in=3600,  # 1시간
    )
    return {"download_url": presigned_url, "expires_in": 3600}
```

---

## 환경변수

```env
MINIO_ENDPOINT=minio:9000
MINIO_ROOT_USER=vibeadmin
MINIO_ROOT_PASSWORD=changeme
MINIO_BUCKET_RAW=raw-uploads
MINIO_BUCKET_CHECKPOINTS=checkpoints
MINIO_BUCKET_OUTPUT=processed-outputs
MINIO_BUCKET_THUMBNAILS=thumbnails
```

---

## requirements.txt 추가 항목

```
boto3==1.34.0
botocore==1.34.0
```

---

## 금지 패턴

```python
# ❌ 파일을 메모리에 전부 읽어서 처리
content = await video.read()          # 대용량 파일 메모리 폭탄
process_in_memory(content)

# ❌ 컨테이너 볼륨으로 파일 공유
# docker-compose.yml에서 shared-volume 마운트로 파일 전달

# ❌ job_id 없는 object key
object_key = f"uploads/{filename}"    # 추적 불가

# ❌ 중간 산출물 저장 생략
result = run_ffmpeg(input)
# MinIO 저장 없이 바로 다음 단계로

# ❌ 처리 후 임시 파일 미삭제
await upload_file(tmp_path, ...)
# os.unlink(tmp_path) 생략 → 디스크 누수

# ✅ 올바른 패턴
with tempfile.TemporaryDirectory() as tmp_dir:
    # 처리
    await upload_file(output, "checkpoints", f"{job_id}/step-01/output.mp4")
# 컨텍스트 종료 시 자동 정리
```
