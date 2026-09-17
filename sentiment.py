import os
import json
import re
import warnings

warnings.filterwarnings(
    "ignore",
    message=".*'_UnionGenericAlias' is deprecated.*",
    category=DeprecationWarning,
    module=r"google\.genai\.types",
)

from google import genai
from google.genai.models import Models


Models._logged_afc_warning = True


MODEL_NAME = "gemini-3.6-flash"
LOCAL_MODEL_NAME = os.getenv(
    "LOCAL_SENTIMENT_MODEL",
    "ProsusAI/finbert",
)
SENTIMENT_CHUNK_SIZE = int(os.getenv("SENTIMENT_CHUNK_SIZE", "800"))
INSTRUCTION = """
Analyse cet article financier ou économique en tenant compte du titre et du texte.
Le score doit mesurer l'impact sur le sentiment des marchés, de l'économie ou de
l'actif concerné, et pas seulement le ton émotionnel de l'article.

Renvoie uniquement un objet JSON valide, sans markdown :
{"sentiment": -1.0, "impact_financier": -1.0, "confiance": 0.0}

Contraintes : sentiment et impact_financier sont compris entre -1 et 1; confiance
est comprise entre 0 et 1. Une valeur positive indique une amélioration, une
valeur négative une détérioration et zéro une information neutre ou équilibrée.
Utilise deux décimales. Evalue l'impact à court terme sur le domaine concerné,
en évitant de confondre une bonne nouvelle pour une entreprise avec une bonne
nouvelle pour l'ensemble du marché.
""".strip()

_local_classifier = None


class QuotaExceededError(RuntimeError):
    """Indique que Gemini a refusé la requête faute de quota disponible."""

    def __init__(
        self,
        message: str,
        *,
        analyzed_count: int = 0,
        remaining_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.analyzed_count = analyzed_count
        self.remaining_count = remaining_count


def parse_score(response_text: str) -> float:
    score_text = response_text.strip()

    if not re.fullmatch(r"-?(?:0[.,]\d{2}|1[.,]00)", score_text):
        raise ValueError(
            f"Réponse invalide : {score_text!r}. "
            "Le format attendu est un nombre entre -1,00 et 1,00."
        )

    score = float(score_text.replace(",", "."))

    if not -1 <= score <= 1:
        raise ValueError(f"Score hors limites : {score}")

    return score


def parse_sentiment_analysis(response_text: str) -> dict[str, float]:
    """Parse and validate the structured Gemini sentiment response."""
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise ValueError("La réponse structurée du modèle n'est pas un JSON valide.") from error

    if not isinstance(result, dict):
        raise ValueError("La réponse structurée doit être un objet JSON.")

    try:
        sentiment = float(result["sentiment"])
        impact = float(result["impact_financier"])
        confidence = float(result["confiance"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("La réponse doit contenir sentiment, impact_financier et confiance.") from error

    if not -1 <= sentiment <= 1 or not -1 <= impact <= 1 or not 0 <= confidence <= 1:
        raise ValueError("Les valeurs de sentiment ou de confiance sont hors limites.")

    score = (0.55 * sentiment + 0.45 * impact) * (0.75 + 0.25 * confidence)
    return {
        "sentiment": round(sentiment, 2),
        "impact_financier": round(impact, 2),
        "confiance": round(confidence, 2),
        "score": round(max(-1.0, min(1.0, score)), 2),
    }


def _split_long_text(text: str, *, max_chars: int = SENTIMENT_CHUNK_SIZE) -> list[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return []

    if len(compact) <= max_chars:
        return [compact]

    sentences = re.split(r"(?<=[.!?])\s+", compact)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(sentence) > max_chars:
                chunks.extend(
                    [sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars)]
                )
                current = ""
            else:
                current = sentence

    if current:
        chunks.append(current)

    return chunks or [compact[:max_chars]]


def prepare_sentiment_chunks(text: str) -> list[str]:
    """Prépare le texte sans le supprimer : les textes longs sont découpés."""
    if SENTIMENT_CHUNK_SIZE < 100:
        raise ValueError("SENTIMENT_CHUNK_SIZE doit être supérieur ou égal à 100.")

    return _split_long_text(text, max_chars=SENTIMENT_CHUNK_SIZE)


def _score_results(results: list[dict[str, object]]) -> tuple[float, float]:
    if results and isinstance(results[0], list):
        results = results[0]

    probabilities = {
        str(result.get("label", "")).lower(): float(result.get("score", 0.0))
        for result in results
    }

    negative_score = sum(
        score
        for label, score in probabilities.items()
        if "negative" in label or label.endswith("_0")
    )
    positive_score = sum(
        score
        for label, score in probabilities.items()
        if "positive" in label or label.endswith("_2")
    )

    return positive_score, negative_score


def analyze_sentiment_local_details(text: str) -> dict[str, float]:
    global _local_classifier

    if _local_classifier is None:
        try:
            from transformers import pipeline
        except ImportError as error:
            raise RuntimeError(
                "Le mode local nécessite transformers, torch et sentencepiece. "
                "Installe-les avec : python3 -m pip install -r requirements.txt"
            ) from error

        _local_classifier = pipeline(
            "sentiment-analysis",
            model=LOCAL_MODEL_NAME,
            tokenizer=LOCAL_MODEL_NAME,
        )

    chunks = prepare_sentiment_chunks(text)
    if not chunks:
        return {"sentiment": 0.0, "impact_financier": 0.0, "confiance": 0.0, "score": 0.0}

    positive_total = 0.0
    negative_total = 0.0
    neutral_total = 0.0
    total_weight = 0

    for chunk in chunks:
        try:
            raw_results = _local_classifier(chunk, top_k=3)
        except Exception as error:
            error_text = str(error).lower()
            if "out of bounds" in error_text or "index" in error_text:
                safe_chunk = chunk[:1200]
                if safe_chunk == chunk:
                    raise
                raw_results = _local_classifier(safe_chunk, top_k=3)
            else:
                raise

        positive_score, negative_score = _score_results(raw_results)
        probabilities = {
            str(result.get("label", "")).lower(): float(result.get("score", 0.0))
            for result in (raw_results[0] if raw_results and isinstance(raw_results[0], list) else raw_results)
        }
        neutral_score = probabilities.get("neutral", 0.0)
        weight = max(len(chunk), 1)
        positive_total += positive_score * weight
        negative_total += negative_score * weight
        neutral_total += neutral_score * weight
        total_weight += weight

    if not positive_total and not negative_total:
        return {"sentiment": 0.0, "impact_financier": 0.0, "confiance": 0.0, "score": 0.0}

    sentiment = (positive_total - negative_total) / (positive_total + negative_total)
    confidence = max(0.0, min(1.0, 1.0 - neutral_total / max(total_weight, 1)))
    impact = sentiment * (0.65 + 0.35 * confidence)
    score = 0.55 * sentiment + 0.45 * impact
    return {
        "sentiment": round(max(-1.0, min(1.0, sentiment)), 2),
        "impact_financier": round(max(-1.0, min(1.0, impact)), 2),
        "confiance": round(confidence, 2),
        "score": round(max(-1.0, min(1.0, score)), 2),
    }


def analyze_sentiment_local(text: str) -> float:
    return analyze_sentiment_local_details(text)["score"]


def analyze_sentiment(text: str) -> float:
    provider = os.getenv("SENTIMENT_PROVIDER", "local").lower()

    if provider == "local":
        return analyze_sentiment_local(text)

    if provider != "gemini":
        raise ValueError(
            f"Fournisseur inconnu : {provider}. Utilise 'gemini' ou 'local'."
        )

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError("La variable GEMINI_API_KEY est absente.")

    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=f"{INSTRUCTION}\n\nTexte : {text}",
        )
    except Exception as error:
        error_text = str(error).upper()
        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            raise QuotaExceededError(
                "Limite Gemini atteinte. Le programme est arrêté."
            ) from error
        raise

    if not response.text:
        raise ValueError("La réponse de l'API est vide.")

    return parse_sentiment_analysis(response.text)["score"]
