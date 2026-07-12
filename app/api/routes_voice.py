from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.pipeline import run_query
from app.models.request import OutputFormat

router = APIRouter(prefix="/api", tags=["voice"])


@router.post("/voice")
async def voice_query(
    db: str = Form(...),
    audio: UploadFile = File(...),
    format: OutputFormat = Form(OutputFormat.table),
):
    """Transcribe + run the NL→SQL pipeline (deprecated — use /transcribe)."""
    try:
        question = await _transcribe(audio)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"STT failed: {e}")
    if not question.strip():
        raise HTTPException(status_code=400, detail="Could not transcribe audio.")
    try:
        return await run_query(
            type(
                "R",
                (),
                {"db": db, "question": question, "format": format, "max_rows": None},
            )()
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Unsafe/invalid query: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Execution failed: {e}")


@router.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    """Return transcribed text only — the frontend puts it in the input box."""
    try:
        text = await _transcribe(audio)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"STT failed: {e}")
    return {"text": text.strip()}


async def _transcribe(audio: UploadFile) -> str:
    import faster_whisper

    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name

    model = faster_whisper.WhisperModel("base", device="cpu")
    segments, _ = model.transcribe(tmp_path)
    return " ".join(s.text for s in segments)
