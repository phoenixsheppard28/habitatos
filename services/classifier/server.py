import os
from contextlib import asynccontextmanager
from threading import Lock
from typing import Annotated

import torch
from fastapi import FastAPI, HTTPException
from gliclass import GLiClassModel, ZeroShotClassificationPipeline
from pydantic import BaseModel, Field
from transformers import AutoTokenizer

MODEL_ID = os.environ.get("GLICLASS_MODEL", "knowledgator/gliclass-small-v1.0")
MODEL_REVISION = os.environ.get("GLICLASS_REVISION", "21edefaf7951f68c68c505f9139ba536d3b448f7")
inference_lock = Lock()


class ClassificationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=16000)
    labels: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(min_length=1, max_length=25)
    threshold: float = Field(default=0.7, ge=0, le=1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    torch.set_num_threads(int(os.environ.get("GLICLASS_THREADS", "2")))
    model = GLiClassModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
    model.eval()
    app.state.tokenizer = tokenizer
    # This checkpoint uses single-label training; sigmoid scores incorrectly accept unrelated labels.
    app.state.pipeline = ZeroShotClassificationPipeline(
        model, tokenizer, classification_type="single-label", device="cpu", max_length=512,
    )
    with torch.inference_mode():
        app.state.pipeline("Daily rainfall observations", ["rainfall", "animal movement"], threshold=0.5)

    yield


app = FastAPI(title="Habitat GLiClass", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID, "revision": MODEL_REVISION}


@app.post("/classify")
def classify(request: ClassificationRequest):
    labels = list(dict.fromkeys(request.labels))
    if any("<<LABEL>>" in label or "<<SEP>>" in label for label in labels):
        raise HTTPException(status_code=422, detail="Labels cannot contain model control tokens.")

    tokenizer = app.state.tokenizer
    prompt = app.state.pipeline.pipe.prepare_input("", labels)
    text_budget = 512 - len(tokenizer.encode(prompt)) - 8
    if text_budget < 32:
        raise HTTPException(status_code=422, detail="Use fewer or shorter labels.")

    text = request.text.replace("<<LABEL>>", " ").replace("<<SEP>>", " ")
    text_tokens = tokenizer.encode(text, add_special_tokens=False, truncation=True, max_length=text_budget)
    text = tokenizer.decode(text_tokens, skip_special_tokens=True)
    with inference_lock, torch.inference_mode():
        predictions = app.state.pipeline(text, labels)[0]

    return {
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "predictions": [{"label": item["label"], "score": float(item["score"])} for item in predictions
                        if item["score"] >= request.threshold],
    }
