import base64
import io
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

MODEL_DIR = Path(__file__).parent / "models_data"
DETECTOR_MODEL = str(MODEL_DIR / "face_detection_yunet_2023mar.onnx")
RECOGNIZER_MODEL = str(MODEL_DIR / "face_recognition_sface_2021dec.onnx")

# Thresholds (tune based on your real data)
COSINE_THRESHOLD = 0.30
L2_THRESHOLD = 1.10

# YuNet tunables (mobile-friendly)
YUNET_SCORE_THRESHOLD = 0.45
YUNET_NMS_THRESHOLD = 0.30
YUNET_TOP_K = 5000
MAX_DETECT_WIDTH = 960  # downscale for stable detection on large phone images


def _decode_image(image_b64: str) -> np.ndarray:
    if "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    decoded = base64.b64decode(image_b64)
    if not decoded:
        raise ValueError("Image data is empty after base64 decode.")

    # Fix EXIF orientation (important for mobile)
    try:
        pil = Image.open(io.BytesIO(decoded))
        pil = ImageOps.exif_transpose(pil).convert("RGB")
        img_rgb = np.array(pil)
        img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
        return img_bgr
    except Exception:
        buf = np.frombuffer(decoded, np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Cannot decode image. Send a valid JPEG or PNG.")
        return img


def _resize_for_detection(img: np.ndarray) -> tuple[np.ndarray, float]:
    """Returns resized image + scale factor (resized_w / orig_w)."""
    h, w = img.shape[:2]
    if w <= MAX_DETECT_WIDTH:
        return img, 1.0
    scale = MAX_DETECT_WIDTH / float(w)
    new_w = int(w * scale)
    new_h = int(h * scale)
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return resized, scale


@lru_cache(maxsize=8)
def _make_detector(input_w: int, input_h: int):
    detector = cv2.FaceDetectorYN.create(
        DETECTOR_MODEL,
        "",
        (input_w, input_h),
        score_threshold=YUNET_SCORE_THRESHOLD,
        nms_threshold=YUNET_NMS_THRESHOLD,
        top_k=YUNET_TOP_K,
    )
    detector.setInputSize((input_w, input_h))
    return detector


@lru_cache(maxsize=1)
def _get_recognizer():
    return cv2.FaceRecognizerSF.create(RECOGNIZER_MODEL, "")


def _detect_faces(orig_bgr: np.ndarray):
    det_img, scale = _resize_for_detection(orig_bgr)
    h, w = det_img.shape[:2]

    detector = _make_detector(w, h)

    # Important: set input size every time in case OpenCV changes internal state
    detector.setInputSize((w, h))

    _, faces = detector.detect(det_img)
    if faces is None or len(faces) == 0:
        return None, det_img, scale

    return faces, det_img, scale


def _scale_face_row_to_original(face_row: np.ndarray, scale: float) -> np.ndarray:
    """
    face_row has 15 values (YuNet):
    [x,y,w,h, l0x,l0y,l1x,l1y,l2x,l2y,l3x,l3y,l4x,l4y, score]
    These are in det_img coordinates. Convert back to original by dividing by scale.
    """
    out = face_row.astype(np.float32).copy()
    if scale == 1.0:
        return out

    # bbox
    out[0] = out[0] / scale
    out[1] = out[1] / scale
    out[2] = out[2] / scale
    out[3] = out[3] / scale

    # landmarks (5 points => 10 numbers)
    for i in range(4, 14):
        out[i] = out[i] / scale

    return out


def _choose_largest_face(faces: np.ndarray) -> np.ndarray:
    # faces[:,2] * faces[:,3] => area
    return faces[np.argmax(faces[:, 2] * faces[:, 3])]


def _normalize_embedding(vec: np.ndarray) -> np.ndarray:
    vec = vec.astype(np.float32).reshape(1, -1)
    n = np.linalg.norm(vec)
    if n > 0:
        vec = vec / n
    return vec


def extract_embedding(image_b64: str) -> list:
    """
    Extract a stable SFace embedding from a single-person image.
    Uses YuNet bbox + landmarks -> alignCrop -> feature.
    """
    orig = _decode_image(image_b64)
    faces, _, scale = _detect_faces(orig)

    if faces is None or len(faces) == 0:
        raise ValueError("No face detected. Ensure good lighting and a clear front-facing photo.")

    best = _choose_largest_face(faces)               # det coords
    best_orig = _scale_face_row_to_original(best, scale)  # orig coords (bbox+landmarks)

    recognizer = _get_recognizer()
    aligned = recognizer.alignCrop(orig, best_orig)  # IMPORTANT: pass landmarks too
    emb = recognizer.feature(aligned)                # shape (1, D)

    emb = _normalize_embedding(emb)
    return emb.flatten().tolist()


def average_embeddings(embeddings: list) -> list:
    """
    Average multiple embeddings (already normalized or not) and re-normalize.
    """
    arr = np.array(embeddings, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] == 0:
        raise ValueError("Embeddings list is empty or invalid.")

    avg = arr.mean(axis=0).reshape(1, -1)
    avg = _normalize_embedding(avg)
    return avg.flatten().tolist()


def match_in_group(group_image_b64: str, stored_embedding: list) -> dict:
    """
    Detect all faces in group photo, compute embedding per face,
    and return best match against stored embedding.
    """
    orig = _decode_image(group_image_b64)
    faces, _, scale = _detect_faces(orig)

    if faces is None or len(faces) == 0:
        return {
            "matched": False,
            "reason": "No faces detected in the group photo.",
            "cosine_score": None,
            "l2_score": None,
            "matched_face_box": None,
            "total_faces": 0,
            "cosine_threshold": COSINE_THRESHOLD,
            "l2_threshold": L2_THRESHOLD,
        }

    recognizer = _get_recognizer()

    stored = np.array(stored_embedding, dtype=np.float32).reshape(1, -1)
    stored = _normalize_embedding(stored)

    best_cosine = -1.0
    best_l2 = float("inf")
    best_box = None

    for f in faces:
        try:
            f_orig = _scale_face_row_to_original(f, scale)

            aligned = recognizer.alignCrop(orig, f_orig)
            emb = recognizer.feature(aligned)
            emb = _normalize_embedding(emb)

            cosine = float(recognizer.match(stored, emb, cv2.FaceRecognizerSF_FR_COSINE))
            l2 = float(recognizer.match(stored, emb, cv2.FaceRecognizerSF_FR_NORM_L2))

            # bbox for UI/debug
            x, y, w, h = f_orig[:4]
            box = [int(x), int(y), int(w), int(h)]

            if cosine > best_cosine:
                best_cosine = cosine
                best_l2 = l2
                best_box = box
        except Exception:
            continue

    matched = (best_cosine >= COSINE_THRESHOLD) and (best_l2 <= L2_THRESHOLD)

    reason = "Match found" if matched else (
        f"No match. Best cosine={best_cosine:.4f} (>= {COSINE_THRESHOLD}), "
        f"best l2={best_l2:.4f} (<= {L2_THRESHOLD})"
    )

    return {
        "matched": matched,
        "cosine_score": round(best_cosine, 4) if best_cosine != -1.0 else None,
        "l2_score": round(best_l2, 4) if best_l2 != float("inf") else None,
        "matched_face_box": best_box if matched else None,
        "total_faces": int(len(faces)),
        "cosine_threshold": COSINE_THRESHOLD,
        "l2_threshold": L2_THRESHOLD,
        "reason": reason,
    }